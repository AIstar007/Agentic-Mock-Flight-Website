import { useCallback, useEffect, useRef, useState } from 'react';

/** All voice I/O is proxied through Sarvam; no browser-native speech API is used. */
export function useSarvamVoice({ apiBase, onTranscript, onError }) {
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const audioRef = useRef(null);
  const audioUrlRef = useRef(null);
  const recordingTimeoutRef = useRef(null);
  const speechGenerationRef = useRef(0);
  const inputGenerationRef = useRef(0);
  const ttsAbortRef = useRef(null);
  const sttAbortRef = useRef(null);
  const onTranscriptRef = useRef(onTranscript);
  const onErrorRef = useRef(onError);
  useEffect(() => { onTranscriptRef.current = onTranscript; }, [onTranscript]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);

  const releaseAudio = useCallback(() => {
    const audio = audioRef.current;
    audioRef.current = null;
    if (audio) {
      try { audio.pause(); audio.currentTime = 0; audio.removeAttribute('src'); audio.load(); } catch {}
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
  }, []);

  const stopSpeaking = useCallback(() => {
    speechGenerationRef.current += 1;
    if (ttsAbortRef.current) ttsAbortRef.current.abort();
    ttsAbortRef.current = null;
    releaseAudio();
  }, [releaseAudio]);

  const cancelVoiceInput = useCallback(() => {
    // Cancels both an active recording and an in-flight transcription when a user switches to typing.
    inputGenerationRef.current += 1;
    if (recordingTimeoutRef.current) clearTimeout(recordingTimeoutRef.current);
    recordingTimeoutRef.current = null;
    if (sttAbortRef.current) sttAbortRef.current.abort();
    sttAbortRef.current = null;
    const recorder = recorderRef.current;
    recorderRef.current = null;
    if (recorder && recorder.state === 'recording') {
      try { recorder.stop(); } catch {}
    }
    streamRef.current?.getTracks().forEach(track => track.stop());
    streamRef.current = null;
    setRecording(false);
    setTranscribing(false);
  }, []);

  const startVoice = useCallback(async () => {
    stopSpeaking();
    if (recorderRef.current && recorderRef.current.state === 'recording') {
      if (recordingTimeoutRef.current) clearTimeout(recordingTimeoutRef.current);
      recordingTimeoutRef.current = null;
      recorderRef.current.stop(); // User clicked the mic again: finish and transcribe this clip.
      return;
    }
    cancelVoiceInput();
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      onErrorRef.current?.('Microphone recording is unavailable in this browser. Allow microphone permission over HTTPS or localhost, or use a supported browser.');
      return;
    }
    const generation = ++inputGenerationRef.current;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      if (generation !== inputGenerationRef.current) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }
      streamRef.current = stream;
      const prefer = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
      const mimeType = prefer.find(type => MediaRecorder.isTypeSupported?.(type));
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      recorderRef.current = recorder;
      const chunks = [];
      recorder.ondataavailable = event => { if (event.data?.size) chunks.push(event.data); };
      recorder.onerror = () => {
        if (generation === inputGenerationRef.current) onErrorRef.current?.('The microphone recording failed. Please retry.');
      };
      recorder.onstop = async () => {
        if (recordingTimeoutRef.current) clearTimeout(recordingTimeoutRef.current);
        recordingTimeoutRef.current = null;
        setRecording(false);
        stream.getTracks().forEach(track => track.stop());
        if (streamRef.current === stream) streamRef.current = null;
        if (recorderRef.current === recorder) recorderRef.current = null;
        if (generation !== inputGenerationRef.current) return;
        const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
        if (!blob.size) { onErrorRef.current?.('No speech was recorded. Please try again.'); return; }
        setTranscribing(true);
        try {
          const dataUrl = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result || ''));
            reader.onerror = () => reject(new Error('Could not read microphone audio.'));
            reader.readAsDataURL(blob);
          });
          if (generation !== inputGenerationRef.current) return;
          const controller = new AbortController();
          sttAbortRef.current = controller;
          const response = await fetch(`${apiBase}/api/v1/voice/transcribe`, {
            method: 'POST', signal: controller.signal,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ audio_base64: dataUrl, content_type: blob.type.split(';')[0] || 'audio/webm', language_code: 'unknown' }),
          });
          let result;
          try { result = await response.json(); } catch { result = {}; }
          if (generation !== inputGenerationRef.current) return;
          if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : `Sarvam transcription failed (${response.status}).`);
          const transcript = String(result.transcript || '').trim();
          if (!transcript) throw new Error('Sarvam did not detect speech. Please retry or type your answer.');
          // Do not deduplicate identical utterances: repeating a value can be an intentional answer to the active slot.
          onTranscriptRef.current?.(transcript, { languageCode: result.language_code || null, requestId: result.request_id || null });
        } catch (error) {
          if (error?.name !== 'AbortError' && generation === inputGenerationRef.current) onErrorRef.current?.(error?.message || 'Sarvam could not transcribe this recording.');
        } finally {
          if (generation === inputGenerationRef.current) setTranscribing(false);
          if (sttAbortRef.current?.signal.aborted || generation === inputGenerationRef.current) sttAbortRef.current = null;
        }
      };
      recorder.start();
      setRecording(true);
      // Sarvam's REST endpoint is for clips under 30 seconds; stop at 25 seconds for headroom.
      recordingTimeoutRef.current = setTimeout(() => {
        if (generation === inputGenerationRef.current && recorderRef.current?.state === 'recording') recorderRef.current.stop();
      }, 25000);
    } catch (error) {
      streamRef.current?.getTracks().forEach(track => track.stop());
      streamRef.current = null;
      setRecording(false);
      if (generation === inputGenerationRef.current) {
        const message = error?.name === 'NotAllowedError' ? 'Microphone permission was denied. Allow microphone access in the browser address bar, then try again.' : error?.message || 'The microphone could not be started.';
        onErrorRef.current?.(message);
      }
    }
  }, [apiBase, cancelVoiceInput, stopSpeaking]);

  const speak = useCallback(async (value, options = {}) => {
    const text = String(value || '').trim();
    if (!text) return;
    stopSpeaking();
    const generation = speechGenerationRef.current;
    const controller = new AbortController();
    ttsAbortRef.current = controller;
    try {
      const response = await fetch(`${apiBase}/api/v1/voice/speak`, {
        method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text.slice(0, 2500), language_code: options.languageCode || 'en-IN' }),
      });
      let result;
      try { result = await response.json(); } catch { result = {}; }
      if (generation !== speechGenerationRef.current) return;
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : `Sarvam speech synthesis failed (${response.status}).`);
      const bytes = Uint8Array.from(atob(result.audio_base64 || ''), char => char.charCodeAt(0));
      const audioUrl = URL.createObjectURL(new Blob([bytes], { type: result.content_type || 'audio/wav' }));
      const audio = new Audio(audioUrl);
      audioRef.current = audio; audioUrlRef.current = audioUrl;
      audio.onended = () => {
        if (audioRef.current === audio) audioRef.current = null;
        if (audioUrlRef.current === audioUrl) { audioUrlRef.current = null; URL.revokeObjectURL(audioUrl); }
      };
      await audio.play();
    } catch (error) {
      if (error?.name !== 'AbortError' && generation === speechGenerationRef.current) onErrorRef.current?.(error?.message || 'Sarvam could not generate voice output.');
    } finally {
      if (ttsAbortRef.current === controller) ttsAbortRef.current = null;
    }
  }, [apiBase, stopSpeaking]);

  useEffect(() => () => {
    try { recorderRef.current?.state === 'recording' && recorderRef.current.stop(); } catch {}
    if (recordingTimeoutRef.current) clearTimeout(recordingTimeoutRef.current);
    if (sttAbortRef.current) sttAbortRef.current.abort();
    if (ttsAbortRef.current) ttsAbortRef.current.abort();
    streamRef.current?.getTracks().forEach(track => track.stop());
    releaseAudio();
  }, [releaseAudio]);

  const supported = typeof navigator !== 'undefined' && Boolean(navigator.mediaDevices?.getUserMedia) && typeof MediaRecorder !== 'undefined';
  return { recording, transcribing, startVoice, cancelVoiceInput, speak, stopSpeaking, supported };
}

const PROMPT_BY_FIELD = {
  confirm_section_edit: 'Do you want to edit the current section? Say yes to change it or no to keep it.',
  confirm_pdp_edit: 'Do you want to edit the passenger details? Say yes to edit them or no to keep the current values.',
  confirm_edit: 'Do you want to edit the current section? Say yes to continue or no to keep the current values.',
  checkin_confirmation: 'Are you ready to complete the synthetic check-in? Say complete mock check-in to run the final required gate.',
  origin: 'Which city or airport are you departing from?', destination: 'Which city or airport are you travelling to?',
  trip_type: 'Is this one-way or a return trip?', departure_date: 'What is your departure date? Use year-month-day or speak the full date.',
  return_date: 'What date would you like to return? It must be on or after departure.', adults: 'How many adult passengers are travelling?',
  children: 'How many children are travelling?', infants: 'How many infants are travelling?', senior_citizens: 'How many senior-citizen passengers are travelling?',
  cabin: 'Which cabin would you like: Economy, Premium Economy, Business or First Class?', title: 'Which title should I use?',
  first_name: 'What is the passenger’s first name, exactly as shown on the travel document?', last_name: 'What is the passenger’s surname, exactly as shown on the travel document?',
  middle_name: 'What middle name should I record? You can say skip if it is not applicable.',
  date_of_birth: 'What is the passenger’s date of birth? Speak the day, month and year, or use year-month-day.',
  nationality: 'What is the passenger’s nationality? Say the full country name or its two- or three-letter ISO code.',
  issuing_country: 'Which country issued the fictional passport? Say the full country name or its two- or three-letter ISO code.',
  gender: 'What gender marker should I record: male, female, X or unspecified?',
  accompanying_adult_index: 'Which adult passenger, age 16 or older on departure, will accompany this infant or under-16 traveller? Say the passenger number.',
  contact_email: 'What email address should the synthetic booking use?', contact_phone: 'What phone number should the synthetic booking use?',
  accepted_terms: 'Do you accept the synthetic demonstration terms? Answer yes or no.', record_locator: 'What is the synthetic booking reference?',
  surname: 'What passenger surname is associated with that demo booking reference?', checkin_eligibility: 'First we must run booking eligibility. Say yes or check my eligibility.',
  passport_number: 'Enter a fictional passport number for the demo. Do not speak a real passport number.', expiry_date: 'What is the fictional passport expiry date? Use day, month and year.',
  purpose_of_stay: 'What is the purpose of stay: tourism, business, transit, study, work, family or medical?',
  duration_of_stay_days: 'How many days is the intended stay? Enter a whole number from 1 to 365.', seat_code: 'Which available demo seat would you like? Say a row number and letter, for example 14 A.',
};

export function speechPromptForCurrentTurn(answer, pendingField = null) {
  // Prefer the live pending field: never replay a previous long answer when the current task asks for a new input.
  if (pendingField && PROMPT_BY_FIELD[pendingField]) return PROMPT_BY_FIELD[pendingField];
  const text = String(answer || '').replace(/\s+/g, ' ').trim();
  if (!text) return '';
  const end = text.lastIndexOf('?');
  if (end >= 0) {
    const boundary = Math.max(text.lastIndexOf('. ', end), text.lastIndexOf('! ', end), text.lastIndexOf('; ', end));
    return text.slice(boundary >= 0 ? boundary + 2 : 0, end + 1).trim();
  }
  return text.split(/(?<=[.!])\s+/)[0].slice(0, 300).trim();
}
