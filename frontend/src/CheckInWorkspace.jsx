import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { speechPromptForCurrentTurn, useSarvamVoice } from './sarvamVoice.js';
import CountryCodeInput, { countryDisplayName } from './CountryCodeInput.jsx';
import {
  AlertTriangle, ArrowLeft, ArrowRight, Check, CheckCircle2, ChevronRight,
  CircleHelp, ClipboardCheck, FileCheck2, Info, LockKeyhole, MessageCircle, Pencil, SkipForward,
  Plane, RefreshCw, Send, ShieldCheck, ShoppingBag, Ticket, UserRound, X, Mic, Volume2
} from 'lucide-react';
import './style.css';

const API = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const FLOW = [
  { id: 'booking', title: 'Booking', label: 'Booking details', icon: Ticket },
  { id: 'documents', title: 'Passenger details', label: 'Passenger & passport', icon: UserRound },
  { id: 'seats', title: 'Seats', label: 'Seat selection', icon: Plane },
  { id: 'extras', title: 'Extras', label: 'Ancillaries', icon: ShoppingBag },
  { id: 'review', title: 'Review', label: 'Review & confirmation', icon: ClipboardCheck },
  { id: 'boarding', title: 'Boarding pass', label: 'Boarding pass', icon: FileCheck2 },
];
const DEMO_DATE = '2034-12-31';

function ConversationCard({ card }) {
  if (!card) return null;
  return <section className={`assistant-result-card assistant-result-${String(card.type || 'workflow').replace(/[^a-z0-9_-]/gi, '')}`}>
    <div className="assistant-result-heading"><strong>{card.title || 'Workflow summary'}</strong>{card.status && <span>{card.status}</span>}</div>
    {Array.isArray(card.fields) && card.fields.length > 0 && <dl>{card.fields.map((field, index) => <div key={`${field.label || 'field'}-${index}`}><dt>{field.label || 'Detail'}</dt><dd>{field.value == null || field.value === '' ? '—' : String(field.value)}</dd></div>)}</dl>}
    {card.note && <p>{card.note}</p>}
  </section>;
}

function fmtHours(value) {
  if (value == null) return '—';
  const h = Number(value);
  if (!Number.isFinite(h)) return '—';
  return `${Math.floor(h)}h ${Math.round((h % 1) * 60)}m`;
}
function fmtMoney(value) { return `AED ${Number(value || 0).toFixed(2)}`; }
function decisionClass(decision) {
  if (decision === 'PASS' || decision === 'COMPLETED' || decision === 'SUCCESS') return 'status-good';
  if (decision === 'FAIL' || decision === 'BLOCKED') return 'status-bad';
  if (decision === 'REVIEW_REQUIRED') return 'status-warn';
  return 'status-neutral';
}

function documentReasonSolution(row) {
  const code = String(row?.evaluation_code || '').toUpperCase();
  const reasons = (row?.reasons || []).join(' ').toLowerCase();
  if (code === 'COUNTRY_CODE_INVALID') return 'Correction: select the country from the searchable country list, or enter its valid ISO-2/ISO-3 code (for example India, IN or IND). The input displays the country name and saves the normalized code.';
  if (code === 'PASSPORT_EXPIRED' || reasons.includes('expires before')) return 'Correction: compare the fictional passport expiry with the full itinerary and enter the actual expiry shown by the demo document. If expired, obtain a valid travel document; do not invent a replacement date to pass validation.';
  if (code === 'PASSPORT_VALIDITY_REVIEW') return 'Resolution: the remaining validity is below this demo’s 180-day screening threshold. This is not a universal rule. Check the real destination, nationality and transit requirements with an authorised current document-check service; keep the gate blocked until resolved.';
  if (code === 'DOCUMENT_DATE_INVALID') return 'Correction: select a valid future passport-expiry date in the date picker. The API expects a valid ISO date; the UI converts the selected date automatically.';
  if (code === 'DOCUMENT_DOB_REQUIRED') return 'Correction: enter the date of birth needed for this child, infant or senior-citizen check. Use the correct source value from the fictional travel document.';
  if (code === 'DOCUMENT_DOB_INVALID' || code === 'DOB_IN_FUTURE') return 'Correction: enter a valid past date of birth. Future DOB values are rejected; do not change a passenger’s age to bypass the rule.';
  if (code === 'DOB_MISMATCH') return 'Correction: compare the booking DOB with the document draft and reconcile the inconsistent value. If the correct source values cannot be reconciled, keep check-in blocked and escalate to an authorised document-check agent.';
  if (code === 'PURPOSE_OF_STAY_MISSING') return 'Correction: select the actual purpose of stay from the dropdown and validate again. Do not select tourism just to bypass a rule.';
  if (code === 'PURPOSE_REQUIRES_DESTINATION_RULES') return 'Resolution: this purpose may require a destination-specific permit (for example work, study, transit, family or medical). This demo has no live nationality/route entry-rule feed and cannot decide which permit is required. Keep check-in blocked and obtain an authorised current Timatic AutoCheck / airline review.';
  if (code === 'DURATION_OF_STAY_INVALID') return 'Correction: enter the intended stay as a whole number from 1 to 365 days and re-run validation.';
  if (code === 'DURATION_EXCEEDS_DEMO_THRESHOLD') return 'Resolution: this stay exceeds the demo’s 90-day screening threshold. Do not falsify the intended duration; obtain authoritative destination-specific entry-duration or permit verification and keep this step blocked until resolved.';
  if (code === 'VISA_REQUIREMENT_REVIEW' || code === 'VISA_EVIDENCE_UNVERIFIED') return 'Resolution: a current nationality-, destination- and transit-specific entry-document decision is needed. This POC does not call official Timatic, so it cannot approve this case. Consult an authorised airline/document-check service.';
  if (code === 'DOCUMENT_MISSING' || code === 'NO_DOCUMENT' || reasons.includes('missing')) return 'Correction: complete the listed mandatory fictional passport number, nationality, issuing country, expiry, purpose and duration fields; provide DOB for any category that requires it. Then re-run validation.';
  if (code === 'PASSPORT_NUMBER_INVALID' || code === 'DOCUMENT_NUMBER_INVALID') return 'Correction: enter the fictional document number using the form rules (5–20 alphanumeric characters). If the source data cannot be reconciled, do not invent a number; keep check-in blocked.';
  if (code === 'NAVITAIRE_DOCUMENT_REJECTED' || code === 'DOCUMENT_CHECK_FAILED') return 'Resolution: the downstream document-check fixture rejected the submitted document. Review all listed passport fields and the specific reason; if the result remains unresolved, escalate to an authorised airline/document-check service. The mock cannot prove authenticity.';
  if (code === 'PURPOSE_OF_STAY_MISSING') return 'Correction: select the actual purpose of stay from the dropdown and validate again. Do not select tourism just to bypass a rule.';
  return 'Resolution: review the reason above, correct an objectively wrong source field if possible, then revalidate. If the rule depends on a current destination/nationality/transit requirement or passport authenticity, keep the gate blocked and escalate to an authorised airline/document-check service. Chat cannot override the decision.';
}

function explainWorkflowError(step, error) {
  const message = String(error?.message || 'The operation could not be completed.');
  const lower = message.toLowerCase();
  let cause = 'The operation returned an error that cannot safely be classified more specifically.';
  let next = 'Keep the workflow blocked, inspect the current step validation details, correct the issue, and retry.';
  if (error?.status === 401 || lower.includes('session token')) {
    cause = 'The demo session is unauthorized or its token is missing or expired.';
    next = 'Reload the synthetic booking with its demo reference and surname. This does not affect a real reservation.';
  } else if (error?.status === 404 || lower.includes('not found') || lower.includes('expired')) {
    cause = 'The synthetic booking/session was not found, may have expired, or the reference/surname pair did not match.';
    next = 'Check the demo reference and surname, then start a new synthetic session.';
  } else if (/passport_expired|expiry.*past|expired document/.test(lower)) {
    cause = 'The mock document rules detected a past-dated or expired document expiry.';
    next = 'Review the fictional expiry date in Passenger details and rerun validation.';
  } else if (/not_verified|visa_evidence_unverified|review_required|review required/.test(lower)) {
    cause = 'The synthetic document evidence is unverified or needs manual review; this demo cannot determine real visa eligibility.';
    next = 'Read each reason code/source note, correct known synthetic input errors, or use the official airline/authorized document-check channel for real travel.';
  } else if (/eligibility|payment|balance|codeshare|carrier|queue|ssr|infant|journey/.test(lower)) {
    cause = 'A deterministic booking rule did not pass. Some checks are reference-derived fixtures, not universal Emirates policy.';
    next = 'Open Booking eligibility rules and review every non-PASS result. Do not bypass the gate; unresolved cases need authorized review.';
  } else if (lower.includes('seat')) {
    cause = 'The seat may be unavailable/already assigned, belong to another journey, or be blocked by an earlier gate.';
    next = 'Refresh the mock seat map and choose another available seat for the correct passenger and journey after the earlier gates pass.';
  } else if (error?.status === 422 || /invalid|required|must|validation/.test(lower)) {
    cause = 'A value or prerequisite did not match the expected schema.';
    next = 'Review the rejected field and required format, complete earlier workflow steps, then submit again.';
  } else if (error?.status === 409 || /blocked|gate|cannot/.test(lower)) {
    cause = 'A workflow precondition failed, so the transaction was safely blocked.';
    next = 'Return to the blocking step, resolve the listed issue, refresh the session, and retry. Chat cannot override the rule.';
  } else if ([502, 503, 504].includes(error?.status) || /timeout|unavailable|connection|network|provider/.test(lower)) {
    cause = 'A backend dependency/service did not respond reliably.';
    next = 'Retry after a short pause. If it persists, check the hosted API health and logs. An unknown provider result is never approval.';
  }
  return "I couldn't complete the " + step + " step.\n\nWhat happened: " + cause + "\n\nWhat to do next: " + next + "\n\nTechnical detail: " + message.slice(0, 350);
}

async function api(path, options = {}, token) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (token) headers['X-Session-Token'] = token;
  const response = await fetch(`${API}${path}`, { ...options, headers, cache: 'no-store' });
  let data;
  try { data = await response.json(); } catch { data = { detail: `Request failed (${response.status})` }; }
  if (!response.ok) {
    const detail = data?.detail;
    // FastAPI/Pydantic often returns a list for field errors. Show schema locations and
    // messages, never the rejected input value (which could contain sensitive data).
    const schemaErrors = Array.isArray(detail) ? detail.map(item => {
      const path = (item.loc || []).filter(part => part !== 'body' && part !== 'query').join('.');
      const friendly = path.replace(/_/g, ' ');
      return (friendly ? friendly + ': ' : '') + (item.msg || 'invalid value');
    }) : [];
    const safeDetail = typeof detail === 'string' ? detail
      : schemaErrors.length ? schemaErrors.join('\n')
      : detail && typeof detail === 'object' && detail.category
        ? `${detail.provider || 'Service'} error (${detail.category}${detail.http_status ? ', HTTP ' + detail.http_status : ''})`
        : `Request failed (${response.status})`;
    const error = new Error(safeDetail);
    error.status = response.status;
    error.detail = detail;
    throw error;
  }
  return data;
}

function CheckInWorkspace({ initialPnr = '', initialSurname = '', onExit = () => {} }) {
  const [activeStep, setActiveStep] = useState('booking');
  const [sid, setSid] = useState(() => sessionStorage.getItem('checkin_demo_sid') || '');
  const [token, setToken] = useState(() => sessionStorage.getItem('checkin_demo_token') || '');
  const [session, setSession] = useState(null);
  const [demoBookings, setDemoBookings] = useState([]);
  const [pnr, setPnr] = useState(initialPnr || 'EM8K2Q');
  const [lastName, setLastName] = useState(initialSurname || 'THOMAS');
  const [documents, setDocuments] = useState({});
  const [seatInventory, setSeatInventory] = useState([]);
  const [passengerForSeat, setPassengerForSeat] = useState('');
  const [journeyForSeat, setJourneyForSeat] = useState('');
  const [confirmedSeatKeys, setConfirmedSeatKeys] = useState([]);
  const [seatAwaitingConfirmation, setSeatAwaitingConfirmation] = useState('');
  const [ancillaryCatalog, setAncillaryCatalog] = useState([]);
  const [ancillarySelection, setAncillarySelection] = useState({});
  const [activeExtrasPassenger, setActiveExtrasPassenger] = useState(0);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatText, setChatText] = useState('');
  const [countryAnswer, setCountryAnswer] = useState('');
  const [chatBusy, setChatBusy] = useState(false);
  const [chatMessages, setChatMessages] = useState([
    { role: 'assistant', text: 'Welcome to the synthetic web check-in assistant. To begin, enter your booking PNR/reference (6–8 characters); I will then ask for the passenger surname before retrieving the demo booking. Use fictional demo data only.' },
  ]);
  const [chatPendingField, setChatPendingField] = useState('record_locator');
  const [chatContext, setChatContext] = useState(() => { try { return JSON.parse(sessionStorage.getItem('checkin_assistant_context') || '{}'); } catch { return {}; } });
  useEffect(() => { try { sessionStorage.setItem('checkin_assistant_context', JSON.stringify(chatContext || {})); } catch {} }, [chatContext]);
  const [chatTargetPassengerKey, setChatTargetPassengerKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState('');
  const [showEvents, setShowEvents] = useState(false);
  const speakNextResponseRef = useRef(false);
  const voiceFollowupRef = useRef(false);
  const interactionTurnRef = useRef(0);
  const lastChatError = useRef('');
  const voice = useSarvamVoice({
    apiBase: API,
    onTranscript: (transcript) => { voiceFollowupRef.current = true; setChatText(transcript); speakNextResponseRef.current = true; sendChat(null, transcript); },
    onError: message => setChatMessages(prev => [...prev, { role: 'assistant', text: message }]),
  });
  const { recording, transcribing, startVoice, cancelVoiceInput, speak, stopSpeaking } = voice;
  const startVoiceInput = () => { voiceFollowupRef.current = true; interactionTurnRef.current += 1; stopSpeaking(); speakNextResponseRef.current = false; startVoice(); };
  const speechAvailable = voice.supported;
  useEffect(() => { setCountryAnswer(''); }, [chatPendingField]);
  useEffect(() => { if (!error) { lastChatError.current = ''; return; } setChatOpen(true); if (lastChatError.current !== error) { lastChatError.current = error; setChatMessages(prev => [...prev, { role: 'assistant', text: explainWorkflowError(activeStep, new Error(error)) }]); } }, [error, activeStep]);

  const passengers = session?.booking?.passengers || [];
  const journeys = session?.booking?.journeys || [];
  const firstJourney = journeys[0] || {};
  const requiredSeatCount = passengers.length * journeys.length;
  const currentStepIndex = FLOW.findIndex(s => s.id === activeStep);
  const ancillaryTotal = useMemo(() => Object.values(ancillarySelection).flat().reduce((sum, code) => {
    const item = ancillaryCatalog.find(x => x.code === code);
    return sum + Number(item?.price || 0);
  }, 0), [ancillarySelection, ancillaryCatalog]);

  const surfaceWorkflowError = (step, err) => {
    setChatOpen(true);
    const detail = err?.message || 'The operation could not be completed.';
    setError(detail); lastChatError.current = detail;
    setChatMessages(prev => [...prev, { role: 'assistant', text: explainWorkflowError(step, err) }]);
  };

  const refreshSession = useCallback(async (sessionId = sid, authToken = token) => {
    if (!sessionId || !authToken) return null;
    const result = await api(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, {}, authToken);
    setSession(result);
    const journeyKeys = (result.booking?.journeys || []).map(j => j.journey_key);
    setJourneyForSeat(current => journeyKeys.includes(current) ? current : (journeyKeys[0] || ''));
    const savedDocs = {};
    for (const passenger of result.booking?.passengers || []) {
      savedDocs[passenger.passenger_key] = {
        passport_number: '',
        nationality: '',
        issuing_country: '',
        date_of_birth: '',
        expiry_date: '',
        purpose_of_stay: 'TOURISM',
        duration_of_stay_days: 7,
        senior_citizen: Boolean(passenger.senior_citizen),
      };
    }
    setDocuments(previous => {
      const next = { ...savedDocs };
      for (const [key, value] of Object.entries(previous)) if (result.booking?.passengers?.some(p => p.passenger_key === key)) next[key] = { ...next[key], ...value };
      for (const [key, value] of Object.entries(result.documents || {})) {
        if (next[key]) next[key] = { ...next[key], ...value, passport_number: next[key].passport_number };
      }
      return next;
    });
    setAncillarySelection(previous => {
      const next = {};
      for (const passenger of result.booking?.passengers || []) {
        const oldCodes = (result.ancillary_selections?.[passenger.passenger_key] || []).map(x => x.code);
        next[passenger.passenger_key] = previous[passenger.passenger_key] || oldCodes;
      }
      return next;
    });
    if (result.checked_in) setActiveStep('boarding');
    else if (result.stage === 'SEATS' && currentStepIndex < 2) setActiveStep('seats');
    else if (result.stage === 'ANCILLARIES' && currentStepIndex < 3) setActiveStep('extras');
    else if (result.stage === 'REVIEW' && currentStepIndex < 4) setActiveStep('review');
    return result;
  }, [sid, token, currentStepIndex]);

  useEffect(() => {
    api('/api/v1/demo-bookings').then(data => setDemoBookings(data.bookings || [])).catch(() => {});
    api('/api/v1/health').then(data => setNotice({ kind: 'info', text: `Backend connected · ${data.mode} · Providers: mock only` })).catch(() => setNotice({ kind: 'warn', text: 'Backend not reachable. Start the FastAPI service on port 8000.' }));
  }, []);

  useEffect(() => {
    if (sid && token) refreshSession(sid, token).catch(() => {
      sessionStorage.removeItem('checkin_demo_sid');
      sessionStorage.removeItem('checkin_demo_token');
      setSid(''); setToken(''); setSession(null);
      setNotice({ kind: 'warn', text: 'Saved demo session expired or is unavailable. Start a new synthetic booking lookup.' });
    });
    // Rehydrate one session at page load; avoid rerunning on every step change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const withBusy = async (fn) => {
    setError(''); setNotice(null); setBusy(true);
    try { await fn(); }
    catch (err) { surfaceWorkflowError(activeStep.replace('_', ' '), err); }
    finally { setBusy(false); }
  };

  const beginBooking = async (event) => {
    event?.preventDefault();
    await withBusy(async () => {
      const data = await api('/api/v1/bookings/lookup', { method: 'POST', body: JSON.stringify({ record_locator: pnr.trim(), last_name: lastName.trim() }) });
      sessionStorage.setItem('checkin_demo_sid', data.session_id);
      sessionStorage.setItem('checkin_demo_token', data.session_token);
      setSid(data.session_id); setToken(data.session_token); setSession(data.session);
      setJourneyForSeat(data.session.booking?.journeys?.[0]?.journey_key || ''); setConfirmedSeatKeys([]); setSeatAwaitingConfirmation('');
      setPassengerForSeat(data.session.booking?.passengers?.[0]?.passenger_key || '');
      setActiveExtrasPassenger(0); setDocuments(Object.fromEntries((data.session.booking?.passengers || []).map(p => [p.passenger_key, { passport_number: '', nationality: '', issuing_country: '', date_of_birth: '', expiry_date: '', purpose_of_stay: 'TOURISM', duration_of_stay_days: 7, senior_citizen: Boolean(p.senior_citizen) }]))); setAncillarySelection({});
      const firstPassenger = (data.session.booking?.passengers || [])[0];
      const firstKey = firstPassenger?.passenger_key || '';
      setChatPendingField('checkin_eligibility');
      setChatTargetPassengerKey(firstKey);
      setChatContext({ target_passenger_key: firstKey, workflow_phase: 'eligibility' });
      setChatMessages(prev => [...prev, { role: 'assistant', text: `Synthetic booking loaded (${data.session.booking.record_locator}). We will proceed in order: (1) booking eligibility, (2) passenger document details and validation, (3) full-cabin seat selection or an explicit skip, (4) optional extras, and (5) final review and mock check-in. First, say “yes” or “check my eligibility” to run the required eligibility gate. You can ask flight FAQs at any point without losing the current step.`, card: { type: 'booking', title: 'Synthetic booking retrieved', status: 'Eligibility required', fields: [{ label: 'Booking reference', value: data.session.booking.record_locator }, { label: 'Passengers', value: String((data.session.booking?.passengers || []).length) }, { label: 'Next step', value: 'Booking eligibility' }], note: 'Fictional demo data only. No live airline system or travel-document authority is contacted.' } }]);
      setActiveStep('documents');
      setNotice({ kind: 'success', text: 'Synthetic booking retrieved through the mock Navitaire adapter.' });
    });
  };

  const loadSeatMap = async (journeyKey = journeyForSeat) => {
    if (!sid || !token) return;
    const query = journeyKey ? `?journey_key=${encodeURIComponent(journeyKey)}` : '';
    const data = await api(`/api/v1/sessions/${sid}/seats${query}`, {}, token);
    setSeatInventory(data.seats || []);
    setPassengerForSeat(current => current || passengers[0]?.passenger_key || '');
  };
  const loadAncillaries = async () => {
    if (!sid || !token) return;
    const data = await api(`/api/v1/sessions/${sid}/ancillaries`, {}, token);
    setAncillaryCatalog(data.items || []);
  };
  useEffect(() => {
    if (activeStep === 'seats' && session) loadSeatMap().catch(err => surfaceWorkflowError('seat inventory', err));
    if (activeStep === 'extras' && session) loadAncillaries().catch(err => surfaceWorkflowError('ancillary catalog', err));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeStep, sid, token, session?.session_id, journeyForSeat]);

  const runEligibility = async (fromChat = false) => withBusy(async () => {
    const data = await api(`/api/v1/sessions/${sid}/eligibility`, { method: 'POST' }, token);
    const updatedSession = await refreshSession();
    const failed = (data.eligibility.rules || []).filter(rule => rule.decision !== 'PASS');
    setNotice({ kind: data.eligibility.decision === 'PASS' ? 'success' : 'warn', text: `Booking eligibility: ${data.eligibility.decision}. ${data.failed_rules} rule(s) require attention.` });
    const details = failed.map(rule => `- ${rule.name} (${rule.decision}): ${rule.message}`).join('\n');
    const explanation = data.eligibility.decision === 'PASS'
      ? 'The synthetic booking eligibility rules passed. Document validation, seat selection and the remaining gates must still pass before the mock check-in can complete. This is not live airline approval.'
      : `Booking eligibility remains ${data.eligibility.decision}; the workflow stays blocked.\n\nReasons:\n${details || 'No individual rule details were returned.'}\n\nNext step: open the eligibility panel, correct any known synthetic data issue, and rerun the rules. Reference-derived rules are not universal Emirates policy and cannot be overridden by chat.`;
    setChatMessages(prev => [...prev, { role: 'assistant', text: explanation, card: { type: 'eligibility', title: 'Booking eligibility', status: data.eligibility.decision, fields: [{ label: 'Eligibility decision', value: data.eligibility.decision }, { label: 'Rules requiring attention', value: String(data.failed_rules ?? failed.length) }], note: 'Synthetic deterministic gate. A pass here does not mean the documents or check-in are approved.' } }]);
    if (fromChat && data.eligibility.decision === 'PASS') {
      const firstPassenger = updatedSession?.booking?.passengers?.[0];
      const existingDocsPass = updatedSession?.document_decision === 'PASS';
      if (!existingDocsPass && firstPassenger?.passenger_key) {
        setActiveStep('documents');
        setChatTargetPassengerKey(firstPassenger.passenger_key);
        setChatPendingField('passport_number');
        setChatContext(prev => ({ ...(prev || {}), target_passenger_key: firstPassenger.passenger_key, workflow_phase: 'documents', completed_document_passengers: [] }));
        setChatMessages(prev => [...prev, { role: 'assistant', text: `Eligibility passed for this synthetic booking. Next, let’s review passenger 1 of ${(updatedSession?.booking?.passengers || []).length}. I will ask for a fictional passport number, nationality and issuing-country code (ISO alpha-2 or alpha-3), date of birth (required for children, infants and senior citizens; regular adults may skip), passport expiry, purpose of stay and duration in days. Verification outcome is computed automatically; there is no manual status override. I’ll show the review and reasons before proceeding.`, card: { type: 'documents', title: 'Passenger document details', status: `Collecting passenger 1 of ${(updatedSession?.booking?.passengers || []).length}`, fields: [{ label: 'Passenger', value: `${firstPassenger.first_name || ''} ${firstPassenger.last_name || ''}`.trim() }, { label: 'Passenger type', value: firstPassenger.passenger_type || 'ADT' }], note: 'Fictional data only. Document validation is a separate required gate.' } }]);
      } else if (existingDocsPass) {
        setActiveStep('seats');
        setChatPendingField(null);
        setChatContext(prev => ({ ...(prev || {}), workflow_phase: 'seats' }));
        setChatMessages(prev => [...prev, { role: 'assistant', text: 'Booking eligibility and document validation have already passed in this synthetic session. Next, choose seats for each passenger and journey, or say “skip seat selection”. I will preserve your existing choices unless you confirm a change.', card: { type: 'eligibility', title: 'Required gates passed', status: 'Eligibility PASS · Documents PASS', fields: [{ label: 'Next section', value: 'Seat selection' }, { label: 'Choice', value: 'Select seats or skip them' }], note: 'Synthetic checks only. Continue with optional extras and the final gate afterward.' } }]);
      }
    } else if (fromChat && data.eligibility.decision !== 'PASS') {
      setChatPendingField('checkin_eligibility');
      setChatContext(prev => ({ ...(prev || {}), workflow_phase: 'eligibility' }));
    }
  });

  const saveAndValidateDocuments = async () => withBusy(async () => {
    const today = new Date().toISOString().slice(0, 10);
    const issues = [];
    for (const [index, passenger] of passengers.entries()) {
      const d = documents[passenger.passenger_key] || {};
      const category = String(passenger.passenger_type || 'ADT').toUpperCase();
      const dobRequired = !['ADT', 'ADULT'].includes(category) || Boolean(passenger.senior_citizen);
      if (!/^[A-Za-z0-9]{5,20}$/.test(String(d.passport_number || ''))) issues.push(`Passenger ${index + 1}: passport number must contain 5–20 letters/digits only.`);
      if (!/^[A-Za-z]{2,3}$/.test(String(d.nationality || ''))) issues.push(`Passenger ${index + 1}: nationality requires an ISO-3166 alpha-2/alpha-3 code such as IN or IND.`);
      if (!/^[A-Za-z]{2,3}$/.test(String(d.issuing_country || ''))) issues.push(`Passenger ${index + 1}: issuing country requires an ISO-3166 alpha-2/alpha-3 code such as AE or ARE.`);
      if (!d.expiry_date || d.expiry_date <= today) issues.push(`Passenger ${index + 1}: passport expiry must be a future date.`);
      if (d.date_of_birth && d.date_of_birth > today) issues.push(`Passenger ${index + 1}: date of birth cannot be in the future.`);
      if (dobRequired && !d.date_of_birth) issues.push(`Passenger ${index + 1}: date of birth is required for this passenger category.`);
      if (passenger.senior_citizen && d.date_of_birth) {
        const dep = new Date((session?.booking?.journeys?.[0]?.departure_date || today) + 'T12:00:00');
        const dob = new Date(d.date_of_birth + 'T12:00:00');
        const age = dep.getFullYear() - dob.getFullYear() - ((dep.getMonth() < dob.getMonth() || (dep.getMonth() === dob.getMonth() && dep.getDate() < dob.getDate())) ? 1 : 0);
        if (age < 60) issues.push(`Passenger ${index + 1}: senior citizen must be at least 60 on departure; calculated age is ${age}.`);
      }
      if (!['TOURISM','BUSINESS','TRANSIT','STUDY','WORK','FAMILY','MEDICAL'].includes(d.purpose_of_stay)) issues.push(`Passenger ${index + 1}: select a valid purpose of stay.`);
      if (!Number.isInteger(Number(d.duration_of_stay_days)) || Number(d.duration_of_stay_days) < 1 || Number(d.duration_of_stay_days) > 365) issues.push(`Passenger ${index + 1}: duration must be a whole number from 1 to 365 days.`);
    }
    if (issues.length) {
      const detail = issues.join(' ');
      setError(detail);
      setChatMessages(prev => [...prev, { role: 'assistant', text: `I have not submitted the document review because the following input rules failed:\n\n${issues.map(item => '• ' + item).join('\n')}\n\nCorrect these fields and retry. The chat action cannot bypass field validation.` }]);
      return;
    }
    const bodyDocs = {};
    for (const passenger of passengers) {
      const doc = documents[passenger.passenger_key] || {};
      bodyDocs[passenger.passenger_key] = {
        passport_number: doc.passport_number,
        nationality: doc.nationality,
        issuing_country: doc.issuing_country,
        date_of_birth: doc.date_of_birth || null,
        expiry_date: doc.expiry_date,
        purpose_of_stay: doc.purpose_of_stay || 'TOURISM',
        duration_of_stay_days: Number(doc.duration_of_stay_days || 7),
        visa_evidence_status: 'NOT_VERIFIED',
        senior_citizen: Boolean(passenger.senior_citizen),
      };
    }
    await api(`/api/v1/sessions/${sid}/documents`, { method: 'POST', body: JSON.stringify({ documents: bodyDocs }) }, token);
    const eligibilityData = await api(`/api/v1/sessions/${sid}/eligibility`, { method: 'POST' }, token);
    const timatic = await api(`/api/v1/sessions/${sid}/documents/validate`, { method: 'POST' }, token);
    const refreshedAfterValidation = await refreshSession();
    setNotice({ kind: timatic.decision === 'PASS' && eligibilityData.eligibility.decision === 'PASS' ? 'success' : 'warn', text: `Eligibility: ${eligibilityData.eligibility.decision}. Mock Timatic-style result: ${timatic.decision}. ${timatic.message}` });
    if (timatic.decision === 'PASS' && eligibilityData.eligibility.decision === 'PASS') {
      setChatPendingField(null);
      setChatContext(prev => ({ ...(prev || {}), workflow_phase: 'seats' }));
      setChatMessages(prev => [...prev, { role: 'assistant', text: 'Passenger and document checks passed in the synthetic environment. Next, choose seats for each passenger and journey, or skip seat selection. These results are mock-only and do not confirm real travel-document eligibility.', card: { type: 'documents', title: 'Passenger & document validation', status: 'PASS · synthetic only', fields: [{ label: 'Booking eligibility', value: eligibilityData.eligibility.decision }, { label: 'Document validation', value: timatic.decision }, { label: 'Passengers evaluated', value: String((timatic.results || []).length) }], note: 'No live Timatic, immigration or passport-authenticity check was performed. Continue to seats or choose “Skip seat selection”.' } }]);
      setActiveStep('seats');
    } else {
      const failedRules = (eligibilityData.eligibility.rules || []).filter(rule => rule.decision !== 'PASS').map(rule => '- ' + rule.name + ': ' + rule.message);
      const storedRows = Object.entries(refreshedAfterValidation?.document_checks || {}).map(([key, row]) => ({
        ...row, passenger_key: row.passenger_key || key.split('#')[0], journey_key: row.journey_key || key.split('#')[1],
      }));
      const validationRows = (Array.isArray(timatic.results) && timatic.results.length ? timatic.results : storedRows);
      const docIssues = validationRows.filter(row => row.decision !== 'PASS').map(row => {
        const reasons = (row.reasons || []).join('; ') || 'The validator did not return a specific reason string.';
        const passengerIndex = passengers.findIndex(passenger => passenger.passenger_key === row.passenger_key);
        const passenger = passengers[passengerIndex];
        const journey = journeys.find(item => item.journey_key === row.journey_key);
        const passengerLabel = passenger ? `Passenger ${passengerIndex + 1} (${`${passenger.first_name || ''} ${passenger.last_name || ''}`.trim()})` : (row.passenger_key || 'Passenger');
        const routeLabel = journey ? `${journey.origin} → ${journey.destination}` : (row.journey_key || 'Selected journey');
        return `${passengerLabel} · ${routeLabel} · ${row.decision} [${row.evaluation_code || 'DOCUMENT_REVIEW'}]\nWhy it failed / needs review: ${reasons}\nRecommended correction / resolution: ${documentReasonSolution(row)}`;
      });
      const eligibilityDetail = (eligibilityData.eligibility.rules || []).filter(rule => rule.decision !== 'PASS').map(rule => {
        const reason = rule.message || 'This rule did not pass.';
        const fix = rule.remediation || rule.action || 'Review the rule inputs and the linked mock rule catalog. Resolve the underlying condition; do not override this gate.';
        return `${rule.name} [${rule.decision}]\nWhy it failed: ${reason}\nRecommended action: ${fix}`;
      });
      const explanation = `CHECK-IN BLOCKED — one or more required checks did not pass. A PASS from one gate cannot override another gate.\n\nBOOKING ELIGIBILITY: ${eligibilityData.eligibility.decision}\n${eligibilityDetail.join('\n\n') || failedRules.join('\n') || 'No individual eligibility rule detail was returned.'}\n\nDOCUMENT VALIDATION: ${timatic.decision}\n${docIssues.join('\n\n') || (timatic.decision !== 'PASS' ? `The validator returned ${timatic.decision} but did not include row-level failure reasons. Treat this as unresolved, do not proceed, and inspect the backend document adapter result/correlation log before retrying.` : (timatic.message || 'No additional document detail was returned.'))}\n\nNEXT ACTION: follow the correction listed under each specific reason. Re-enter only objectively correct document data and validate again. When a current destination-specific rule or official check is needed, keep the journey blocked and escalate to an authorised airline/document-check service. This local ruleset is not IATA Timatic and is not a live immigration decision.`;
      setChatMessages(prev => [...prev, { role: 'assistant', text: explanation, card: {
        type: 'documents', title: 'Passenger & document validation', status: `${timatic.decision} · workflow blocked`,
        fields: [
          { label: 'Booking eligibility', value: eligibilityData.eligibility.decision },
          { label: 'Document validation', value: timatic.decision },
          ...eligibilityDetail.slice(0, 2).map((item, i) => ({ label: `Eligibility reason ${i + 1}`, value: item })),
          ...docIssues.slice(0, 4).map((item, i) => ({ label: `Document failure / resolution ${i + 1}`, value: item })),
        ],
        note: 'The chatbot includes a reason and a correction or escalation for each non-PASS document result. Resolve and revalidate; the chat cannot override a failed or unresolved gate.'
      } }]);
      setChatContext(prev => ({ ...(prev || {}), workflow_phase: 'documents' }));
    }
  });

  const selectSeat = async (seatCode) => withBusy(async () => {
    const data = await api(`/api/v1/sessions/${sid}/seats`, { method: 'POST', body: JSON.stringify({ passenger_key: passengerForSeat, journey_key: journeyForSeat, seat_code: seatCode }) }, token);
    await refreshSession();
    setSeatInventory(prev => prev.map(s => s.seat_code === data.seat_code ? { ...s, available: false } : s));
    const assignmentKey = `${passengerForSeat}#${journeyForSeat}`;
    setSeatAwaitingConfirmation(assignmentKey);
    setNotice({ kind: 'success', text: `Seat ${data.seat_code} is saved for this synthetic booking. Confirm it below to move to the next passenger/journey, or choose another seat before confirming.` });
  });

  const confirmSeatFor = async (passengerKey = passengerForSeat, journeyKey = journeyForSeat) => withBusy(async () => {
    const currentKey = `${passengerKey}#${journeyKey}`;
    const updated = await refreshSession();
    if (!updated?.seat_assignments?.[currentKey]?.seat_code) {
      setNotice({ kind: 'warn', text: 'Choose an available seat or skip this passenger/journey before confirming.' });
      return;
    }
    const confirmed = new Set([...confirmedSeatKeys, currentKey]); setConfirmedSeatKeys([...confirmed]); setSeatAwaitingConfirmation('');
    const skipped = new Set(updated?.seat_selection_skipped_assignments || []);
    const nextPair = journeys.flatMap(j => passengers.map(p => ({ passenger: p, journey: j, key: `${p.passenger_key}#${j.journey_key}` }))).find(pair => !confirmed.has(pair.key) && !skipped.has(pair.key));
    if (nextPair) {
      setPassengerForSeat(nextPair.passenger.passenger_key); setJourneyForSeat(nextPair.journey.journey_key);
      setChatTargetPassengerKey(nextPair.passenger.passenger_key);
      setChatContext(prev => ({ ...(prev || {}), target_passenger_key: nextPair.passenger.passenger_key, selected_passenger_key: nextPair.passenger.passenger_key, selected_journey_key: nextPair.journey.journey_key, workflow_phase: 'seats' }));
      try { await loadSeatMap(nextPair.journey.journey_key); } catch {}
      setNotice({ kind: 'success', text: `Seat confirmed. Next is passenger ${passengers.findIndex(p=>p.passenger_key===nextPair.passenger.passenger_key)+1} on ${nextPair.journey.origin}–${nextPair.journey.destination}.` });
    } else {
      setNotice({ kind: 'success', text: 'Every passenger/journey now has a confirmed seat or an explicit skip. Proceeding to extras.' });
      setActiveStep('extras');
    }
  });

  const skipPassengerSeat = async (passengerKey = passengerForSeat, journeyKey = journeyForSeat) => withBusy(async () => {
    await api(`/api/v1/sessions/${sid}/seats/skip`, { method: 'POST', body: JSON.stringify({ passenger_key: passengerKey, journey_key: journeyKey }) }, token);
    const updated = await refreshSession();
    const currentKey = `${passengerKey}#${journeyKey}`;
    const confirmed = new Set([...confirmedSeatKeys, currentKey]); setConfirmedSeatKeys([...confirmed]); setSeatAwaitingConfirmation('');
    const skipped = new Set(updated?.seat_selection_skipped_assignments || []);
    const nextPair = journeys.flatMap(j => passengers.map(p => ({ passenger: p, journey: j, key: `${p.passenger_key}#${j.journey_key}` }))).find(pair => !confirmed.has(pair.key) && !skipped.has(pair.key));
    if (nextPair) { setPassengerForSeat(nextPair.passenger.passenger_key); setJourneyForSeat(nextPair.journey.journey_key); setChatTargetPassengerKey(nextPair.passenger.passenger_key); setChatContext(prev=>({...prev,target_passenger_key:nextPair.passenger.passenger_key,selected_passenger_key:nextPair.passenger.passenger_key,selected_journey_key:nextPair.journey.journey_key,workflow_phase:'seats'})); try { await loadSeatMap(nextPair.journey.journey_key); } catch {} }
    else setActiveStep('extras');
    setNotice({ kind: 'info', text: `Seat skipped for ${passengers.find(p=>p.passenger_key===passengerKey)?.first_name || 'passenger'} on the selected journey. Other assignments were preserved.` });
  });

  const saveAncillaries = async (passenger, { advance = false, skip = false } = {}) => withBusy(async () => {
    const idx = passengers.findIndex(p => p.passenger_key === passenger.passenger_key);
    const codes = skip ? [] : (ancillarySelection[passenger.passenger_key] || []);
    await api(`/api/v1/sessions/${sid}/ancillaries`, { method: 'POST', body: JSON.stringify({ passenger_key: passenger.passenger_key, ancillary_codes: codes }) }, token);
    if (skip) setAncillarySelection(prev => ({ ...prev, [passenger.passenger_key]: [] }));
    await refreshSession();
    if (advance && idx >= 0 && idx + 1 < passengers.length) {
      const nextPax = passengers[idx + 1]; setActiveExtrasPassenger(idx + 1); setChatTargetPassengerKey(nextPax.passenger_key);
      setChatContext(prev => ({ ...(prev || {}), target_passenger_key: nextPax.passenger_key, selected_passenger_key: nextPax.passenger_key, active_extras_passenger_index: idx + 1, workflow_phase: 'extras' }));
      setNotice({ kind: 'success', text: `${skip ? 'Skipped' : 'Confirmed'} optional extras for passenger ${idx + 1}. Now selecting extras for passenger ${idx + 2} of ${passengers.length}.` });
    } else if (advance) {
      setActiveExtrasPassenger(Math.max(0, idx));
      setNotice({ kind: 'success', text: `${skip ? 'Skipped' : 'Confirmed'} optional extras for the final passenger. Review all passengers, then select Confirm extras to continue.` });
    } else setNotice({ kind: 'success', text: `Ancillary preferences saved for ${passenger.first_name}.` });
  });

  const confirmExtras = async () => withBusy(async () => {
    // Persist the visible selection for every passenger before the server-side confirmation gate.
    for (const passenger of passengers) {
      await api(`/api/v1/sessions/${sid}/ancillaries`, { method: 'POST', body: JSON.stringify({ passenger_key: passenger.passenger_key, ancillary_codes: ancillarySelection[passenger.passenger_key] || [] }) }, token);
    }
    await api(`/api/v1/sessions/${sid}/ancillaries/confirm`, { method: 'POST' }, token);
    const updated = await refreshSession(); setActiveStep('review');
    setChatContext(prev => ({ ...(prev || {}), workflow_phase: 'review' }));
    setChatPendingField(null);
    setChatMessages(prev => [...prev, { role: 'assistant', text: 'The optional-services step is saved and explicitly confirmed. Review your synthetic itinerary, passenger list, seat assignments (or your choice to skip seats), and extras below. Say “complete mock check-in” only when you are ready for the final server-side gate.', card: { type: 'extras', title: 'Optional extras confirmed', status: 'CONFIRMED · synthetic', fields: [{ label: 'Passengers', value: String((updated?.booking?.passengers || []).length) }, { label: 'Seat selection', value: updated?.seat_selection_skipped ? 'Skipped by passenger' : `${Object.keys(updated?.seat_assignments || {}).length} assignment(s)` }, { label: 'Extras stage', value: 'Confirmed' }], note: 'No real service was purchased or transmitted to an airline. Review all entries before mock check-in.' } }]);
    setNotice({ kind: 'success', text: 'Ancillary step explicitly confirmed. Continue to the final review.' });
  });

  const completeCheckin = async () => withBusy(async () => {
    const data = await api(`/api/v1/sessions/${sid}/check-in`, { method: 'POST' }, token);
    await refreshSession(); setActiveStep('boarding');
    setChatPendingField(null); setChatContext(prev => ({ ...(prev || {}), workflow_phase: 'boarding' }));
    setChatMessages(prev => [...prev, { role: 'assistant', text: 'The synthetic check-in flow completed successfully. A mock boarding-pass record is now available on the page. This is not valid for travel and was not issued by Emirates or any airline.', card: { type: 'boarding', title: 'Mock check-in complete', status: 'COMPLETED · demo only', fields: [{ label: 'Boarding-pass records', value: String((data.boarding_passes || []).length) }, { label: 'Seat choice', value: session?.seat_selection_skipped ? 'Skipped · no seat assigned' : 'See passenger boarding-pass records' }, { label: 'Live airline approval', value: 'Not performed' }], note: 'Synthetic artefact only. Do not use this record for travel.' } }]);
    setNotice({ kind: 'success', text: `${data.boarding_passes.length} synthetic boarding pass record(s) generated. Not valid for travel.` });
  });

  const sendChat = async (event, overrideText = null) => {
    event?.preventDefault();
    const text = String(overrideText ?? chatText).trim();
    if (!text || chatBusy) return;
    const submittedTurn = ++interactionTurnRef.current;
    const shouldSpeakResponse = speakNextResponseRef.current || voiceFollowupRef.current;
    stopSpeaking();
    setChatText(''); setChatBusy(true); setError('');
    setChatMessages(prev => [...prev, { role: 'user', text }]);
    try {
      const targetKey = chatTargetPassengerKey || passengers[0]?.passenger_key || '';
      const data = await api('/api/v1/chat', { method: 'POST', body: JSON.stringify({
        message: text, session_id: sid || null, pending_field: chatPendingField,
        context: {
          ...chatContext,
          target_passenger_key: targetKey,
          document_draft: documents[targetKey] || {},
          selected_passenger_key: passengerForSeat || targetKey,
          selected_journey_key: journeyForSeat,
          active_step: activeStep, active_page: activeStep,
          page_state: { pnr, surname: lastName, session_stage: session?.stage || 'BOOKING_LOOKUP', eligibility: session?.eligibility?.decision || null, document_decision: session?.document_decision || null, checked_in: Boolean(session?.checked_in), passenger_count: passengers.length, journey_count: journeys.length, selected_passenger_key: passengerForSeat || targetKey, selected_journey_key: journeyForSeat },
          page_error: error || null, page_notice: typeof notice === 'string' ? notice : notice?.text || null,
          ancillary_selection: ancillarySelection,
          active_extras_passenger_index: activeExtrasPassenger,
          workflow_phase: chatContext.workflow_phase || activeStep,
        },
      }) }, token || undefined);
      const documentAction = (data.actions || []).find(action => action.type === 'UPDATE_DOCUMENT_FIELDS');
      const cardTargetKey = data.target_passenger_key || documentAction?.passenger_key || targetKey;
      const mergedDocument = { ...(documents[cardTargetKey] || {}), ...(documentAction?.fields || {}) };
      const cardPassenger = passengers.find(passenger => passenger.passenger_key === cardTargetKey);
      let chatCard = data.card || null;
      if (documentAction) {
        const category = String(cardPassenger?.passenger_type || 'ADT').toUpperCase();
        const dobRequired = ['CHD','CHILD','INF','INFANT','SENIOR','SENIOR_CITIZEN'].includes(category) || Boolean(mergedDocument.senior_citizen) || Boolean(cardPassenger?.senior_citizen);
        const countryNamesByPassenger = {};
        await Promise.all(passengers.map(async passenger => {
          const doc = passenger.passenger_key === cardTargetKey ? mergedDocument : (documents[passenger.passenger_key] || {});
          countryNamesByPassenger[passenger.passenger_key] = {
            nationality: await countryDisplayName(API, doc.nationality),
            issuing_country: await countryDisplayName(API, doc.issuing_country),
          };
        }));
        const docFields = (passenger, doc) => {
          const type = String(passenger?.passenger_type || 'ADT').toUpperCase();
          const countryNames = countryNamesByPassenger[passenger?.passenger_key] || {};
          const requiredDob = ['CHD','CHILD','INF','INFANT','SENIOR','SENIOR_CITIZEN'].includes(type) || Boolean(doc?.senior_citizen) || Boolean(passenger?.senior_citizen);
          return [
            { label: 'Passenger', value: `${passenger?.title || ''} ${passenger?.first_name || ''} ${passenger?.last_name || ''}`.trim() || passenger?.passenger_key || 'Passenger' },
            { label: 'Passenger category', value: type },
            { label: 'Fictional passport number', value: doc?.passport_number || 'Not entered' },
            { label: 'Nationality', value: countryNames.nationality || doc?.nationality || 'Not entered' },
            { label: 'Issuing country', value: countryNames.issuing_country || doc?.issuing_country || 'Not entered' },
            { label: 'Date of birth', value: doc?.date_of_birth || (requiredDob ? 'Required for this passenger category · not entered' : 'Optional for a regular adult · not entered') },
            { label: 'Passport expiry', value: doc?.expiry_date || 'Not entered' },
            { label: 'Purpose of stay', value: doc?.purpose_of_stay || 'Not entered' },
            { label: 'Duration of stay', value: doc?.duration_of_stay_days ? `${doc.duration_of_stay_days} days` : 'Not entered' },
          ];
        };
        const otherDocs = passengers.filter(passenger => passenger.passenger_key !== cardTargetKey).map(passenger => ({ passenger, doc: documents[passenger.passenger_key] || {} }));
        const isLastField = !data.pending_field;
        const allPassengersEntered = isLastField && otherDocs.every(({doc}) => String(doc.passport_number || '').trim().length >= 5) && String(mergedDocument.passport_number || '').trim().length >= 5;
        const allFields = isLastField
          ? passengers.flatMap(passenger => docFields(passenger, passenger.passenger_key === cardTargetKey ? mergedDocument : (documents[passenger.passenger_key] || {})))
          : docFields(cardPassenger, mergedDocument);
        chatCard = { type: 'document_draft', title: isLastField ? 'Passenger document summary' : 'Passenger document details', status: data.pending_field ? 'Draft · more fields required' : (allPassengersEntered ? 'All passenger entries collected · review' : `Passenger ${(passengers.findIndex(p => p.passenger_key === cardTargetKey) + 1)} captured · other passengers may remain`), fields: allFields, note: 'Synthetic test values only. Review every listed field before running mock document validation; missing or invalid required fields will block validation. No live Timatic or immigration service is consulted.' };
      } else if (!chatCard && data.intent === 'faq') {
        chatCard = { type: 'faq', title: 'Flight information', status: data.citations?.length ? 'Sources available' : 'Evidence-limited', note: 'This answer leaves your active check-in step and pending field unchanged.' };
      }
      const assistantText = data.intent === 'slot_filled' && !data.pending_field && documentAction
        ? (() => {
            const nextPax = passengers.find(passenger => passenger.passenger_key !== cardTargetKey && !(String((documents[passenger.passenger_key] || {}).passport_number || '').trim().length >= 5));
            return nextPax
              ? `${data.answer}\n\nBefore document validation, continue with passenger ${passengers.findIndex(passenger => passenger.passenger_key === nextPax.passenger_key) + 1} of ${passengers.length}. Say “next passenger” to collect their document fields. When all passengers are complete, say “validate documents”.`
              : `${data.answer}\n\nAll passenger entries are present in the summary card. Review them, then say “validate documents” to run the required mock checks.`;
          })()
        : data.answer;
      setChatMessages(prev => [...prev, { role: 'assistant', text: assistantText, citations: data.citations || [], card: chatCard }]);
      if (shouldSpeakResponse && (speakNextResponseRef.current || voiceFollowupRef.current) && submittedTurn === interactionTurnRef.current && data.answer) {
        speak(speechPromptForCurrentTurn(data.answer, data.pending_field));
      }
      if (shouldSpeakResponse) speakNextResponseRef.current = false;
      if (Object.prototype.hasOwnProperty.call(data, 'pending_field')) setChatPendingField(data.pending_field);
      if (data.context) setChatContext(data.context);
      if (data.target_passenger_key) setChatTargetPassengerKey(data.target_passenger_key);
      for (const action of data.actions || []) {
        if (action.type === 'UPDATE_DOCUMENT_FIELDS') {
          setDocuments(prev => ({ ...prev, [action.passenger_key]: { ...(prev[action.passenger_key] || {}), ...(action.fields || {}) } }));
          setChatTargetPassengerKey(action.passenger_key);
        } else if (action.type === 'NAVIGATE_STEP' && action.target) {
          setActiveStep(action.target);
        } else if (action.type === 'REQUEST_DOCUMENT_VALIDATION') {
          await saveAndValidateDocuments();
        } else if (action.type === 'RUN_ELIGIBILITY') {
          await runEligibility(true);
        } else if (action.type === 'LOAD_SEAT_MAP') {
          setActiveStep('seats');
          if (action.journey_key) setJourneyForSeat(action.journey_key);
          try { await loadSeatMap(action.journey_key || journeyForSeat); }
          catch (err) { surfaceWorkflowError('seat inventory', err); }
        } else if (action.type === 'SEAT_SELECTED') {
          const paxKey = action.passenger_key || targetKey;
          const journeyKey = action.journey_key || journeyForSeat;
          setActiveStep('seats'); setPassengerForSeat(paxKey); setJourneyForSeat(journeyKey); setChatTargetPassengerKey(paxKey); setChatContext(prev => ({ ...(prev || {}), target_passenger_key: paxKey, selected_passenger_key: paxKey, selected_journey_key: journeyKey, workflow_phase: 'seats' })); setSeatAwaitingConfirmation(`${paxKey}#${journeyKey}`);
          try { await refreshSession(); await loadSeatMap(journeyKey); }
          catch (err) { surfaceWorkflowError('seat inventory refresh', err); }
        } else if (action.type === 'SEAT_CONFIRMED') {
          await confirmSeatFor(action.passenger_key || passengerForSeat, action.journey_key || journeyForSeat);
        } else if (action.type === 'UPDATE_CHECKIN_ANCILLARIES') {
          const codes = action.codes || [];
          const keys = action.passenger_keys || [];
          setAncillarySelection(prev => {
            const next = { ...prev };
            for (const key of keys) {
              const current = next[key] || [];
              if (action.replace) next[key] = [];
              else if (action.remove) next[key] = current.filter(code => !codes.includes(code));
              else next[key] = [...new Set([...current, ...codes])];
            }
            return next;
          });
          if (Number.isInteger(action.passenger_index)) setActiveExtrasPassenger(action.passenger_index);
          if (keys[0]) setChatTargetPassengerKey(keys[0]);
          setActiveStep('extras');
        } else if (action.type === 'SAVE_AND_ADVANCE_CHECKIN_ANCILLARIES' || action.type === 'SKIP_AND_ADVANCE_CHECKIN_ANCILLARIES') {
          const pax = passengers.find(p => p.passenger_key === action.passenger_key);
          if (pax) await saveAncillaries(pax, { advance: true, skip: action.type === 'SKIP_AND_ADVANCE_CHECKIN_ANCILLARIES' });
          setActiveStep('extras');
        } else if (action.type === 'SKIP_SEAT_PASSENGER') {
          await skipPassengerSeat(action.passenger_key, action.journey_key);
        } else if (action.type === 'CONFIRM_ANCILLARIES') {
          await confirmExtras();
        }
      }
      if (data.session_id && data.session_token) {
        sessionStorage.setItem('checkin_demo_sid', data.session_id);
        sessionStorage.setItem('checkin_demo_token', data.session_token);
        setSid(data.session_id); setToken(data.session_token);
        const targetPassenger = data.target_passenger_key || data.session?.booking?.passengers?.[0]?.passenger_key || '';
        setChatPendingField(data.pending_field ?? null);
        setChatContext(data.context || (targetPassenger ? { target_passenger_key: targetPassenger, workflow_phase: 'eligibility' } : {}));
        setChatTargetPassengerKey(targetPassenger);
        const loaded = await refreshSession(data.session_id, data.session_token);
        setChatTargetPassengerKey(loaded?.booking?.passengers?.[0]?.passenger_key || targetPassenger);
        setActiveStep(data.actions?.some(action => action.type === 'NAVIGATE_STEP' && action.target === 'documents') ? 'documents' : activeStep);
      } else if (sid && token && data.intent !== 'faq') {
        const updated = await refreshSession();
        if (updated?.checked_in) setActiveStep('boarding');
      }
      if (data.intent === 'document_validation' && data.actions?.[0]?.decision !== 'PASS') {
        const updated = await refreshSession();
        const rows = Object.values(updated?.document_checks || {}).filter(row => row.decision !== 'PASS');
        const details = rows.map(row => '- ' + (row.evaluation_code || row.decision) + ': ' + (row.reasons || []).join('; ')).join('\\n');
        setChatMessages(prev => [...prev, { role: 'assistant', text: 'The document gate is ' + (data.actions?.[0]?.decision || 'not PASS') + '.\\n\\nReasons:\\n' + (details || 'Review the document panel for the per-passenger/per-journey outcome.') + '\\n\\nThe gate stays closed until blocking items are resolved. This mock result is not real visa or passport validation.' }]);
      }
      if (data.intent === 'eligibility' && data.actions?.[0]?.decision !== 'PASS') {
        const updated = await refreshSession();
        const failed = (updated?.eligibility?.rules || []).filter(rule => rule.decision !== 'PASS');
        const details = failed.map(rule => '- ' + rule.name + ': ' + rule.message).join('\\n');
        setChatMessages(prev => [...prev, { role: 'assistant', text: 'Booking eligibility is not PASS, so later check-in steps remain blocked.\\n\\nReasons:\\n' + (details || 'Open the eligibility panel for rule-level detail.') + '\\n\\nReview the synthetic booking fields and rerun only after correcting the demo data.' }]);
      }
    } catch (err) {
      surfaceWorkflowError('chat/workflow command', err);
      if (shouldSpeakResponse && submittedTurn === interactionTurnRef.current) speak(speechPromptForCurrentTurn(explainWorkflowError('chat/workflow command', err), chatPendingField));
      if (shouldSpeakResponse) speakNextResponseRef.current = false;
    } finally { setChatBusy(false); }
  };

  const clearSession = () => {
    sessionStorage.removeItem('checkin_demo_sid'); sessionStorage.removeItem('checkin_demo_token');
    setSid(''); setToken(''); setSession(null); setDocuments({}); setChatPendingField(null); setChatContext({}); setChatTargetPassengerKey(''); setSeatInventory([]); setAncillarySelection({}); setJourneyForSeat(''); setPassengerForSeat(''); setConfirmedSeatKeys([]); setSeatAwaitingConfirmation('');
    setActiveExtrasPassenger(0); setChatPendingField('record_locator'); setChatContext({}); setChatMessages([{ role: 'assistant', text: 'Enter the 6–8 character synthetic booking PNR/reference. I will ask for the passenger surname next. Fictional demo data only.' }]); setActiveStep('booking'); setError(''); setNotice({ kind: 'info', text: 'Local demo session cleared from this browser. This does not delete provider-side records.' });
  };

  const goTo = (step) => {
    if (step === 'booking') { setActiveStep(step); return; }
    if (!session) { setNotice({ kind: 'warn', text: 'Retrieve a synthetic booking first.' }); return; }
    if (step === 'documents') setActiveStep(step);
    else if (step === 'seats' && session.document_decision === 'PASS' && session.eligibility?.decision === 'PASS') setActiveStep(step);
    else if (step === 'extras' && session.document_decision === 'PASS' && session.eligibility?.decision === 'PASS' && (session.seat_selection_skipped || (Object.keys(session.seat_assignments || {}).length + (session.seat_selection_skipped_assignments || []).length) >= requiredSeatCount)) setActiveStep(step);
    else if (step === 'review' && session.ancillary_stage_complete) setActiveStep(step);
    else if (step === 'boarding' && session.checked_in) setActiveStep(step);
    else setNotice({ kind: 'warn', text: 'This step is locked until the preceding eligibility and workflow gates pass.' });
  };

  const renderNotice = () => notice && <div className={`notice notice-${notice.kind}`}><Info size={17}/><span>{notice.text}</span><button className="icon-button" onClick={() => setNotice(null)} aria-label="Dismiss message"><X size={15}/></button></div>;
  const renderBooking = () => <div className="page-stack">
    <div className="page-heading"><div><div className="eyebrow">WELCOME ABOARD</div><h1>Online check-in</h1><p>Manage your synthetic booking and move through each check-in step.</p></div><div className="hero-plane"><Plane size={27}/></div></div>
    <div className="panel">
      <div className="section-title"><div><span className="step-kicker">STEP 01</span><h2>Find your booking</h2><p>Enter the booking reference and the passenger's surname.</p></div><Ticket className="section-icon" size={25}/></div>
      <form onSubmit={beginBooking} className="form-grid">
        <label>Booking reference<input value={pnr} onChange={e => setPnr(e.target.value.toUpperCase())} minLength={6} maxLength={8} required autoComplete="off" placeholder="e.g. EM8K2Q"/></label>
        <label>Passenger surname<input value={lastName} onChange={e => setLastName(e.target.value.toUpperCase())} required maxLength={64} autoComplete="family-name" placeholder="Surname"/></label>
        <div className="form-actions"><button className="btn btn-primary" type="submit" disabled={busy}>{busy ? <RefreshCw className="spin" size={17}/> : <LockKeyhole size={17}/>} Retrieve booking <ArrowRight size={16}/></button></div>
      </form>
    </div>
    <div className="panel demo-panel">
      <div className="section-title"><div><h2>Explore demo scenarios</h2><p>These are synthetic fixtures for testing success and failure paths.</p></div><ShieldCheck className="section-icon" size={24}/></div>
      <div className="scenario-grid">{demoBookings.map(b => <button className="scenario" key={b.record_locator} onClick={() => { setPnr(b.record_locator); setLastName(b.last_name); setNotice({ kind: 'info', text: `Loaded ${b.record_locator}: ${b.notice}` }); }}><span className="scenario-code">{b.record_locator}</span><span className="scenario-route">{b.route}</span><span className="scenario-desc">{b.notice}</span><span className="scenario-action">Use scenario <ChevronRight size={14}/></span></button>)}</div>
    </div>
    <div className="panel process-panel"><div className="section-title"><div><h2>Your journey</h2><p>Every action runs through a backend agent and a deterministic service gate.</p></div></div><div className="journey-strip">{FLOW.map((item, idx) => <div className="journey-item" key={item.id}><span className="journey-number">{String(idx + 1).padStart(2,'0')}</span><span>{item.title}</span>{idx < FLOW.length - 1 && <ChevronRight size={13}/>}</div>)}</div></div>
  </div>;

  const setDocField = (paxKey, field, value) => setDocuments(prev => ({ ...prev, [paxKey]: { ...(prev[paxKey] || {}), [field]: value } }));
  const renderDocuments = () => <div className="page-stack">
    <div className="page-heading"><div><div className="eyebrow">STEP 02 · PASSENGER DETAILS</div><h1>Passenger & travel documents</h1><p>Enter synthetic passport details, destination purpose and stay duration. The document outcome is calculated automatically from the available mock rule set.</p></div><div className="hero-plane"><UserRound size={25}/></div></div>
    {session?.eligibility && <div className={`decision-banner ${decisionClass(session.eligibility.decision)}`}><ShieldCheck size={20}/><div><strong>Booking eligibility: {session.eligibility.decision}</strong><p>{session.eligibility.policy_notice || 'Reference-derived synthetic checks only.'}</p></div><button className="btn btn-light btn-small" onClick={runEligibility} disabled={busy}>Re-run rules</button></div>}
    <div className="disclaimer"><AlertTriangle size={17}/><span>Use fictional values only. The document adapter does not query Timatic, validate passport authenticity or determine legal permission to travel. Purpose-of-stay and duration checks are deterministic local rules only; this cannot substitute for a current Timatic lookup.</span></div>
    {passengers.map((p, idx) => {
      const doc = documents[p.passenger_key] || { passport_number: '', nationality: '', issuing_country: '', date_of_birth: '', expiry_date: '', purpose_of_stay: 'TOURISM', duration_of_stay_days: 7, senior_citizen: Boolean(p.senior_citizen) };
      const passengerType = String(p.passenger_type || 'ADT').toUpperCase();
      const requiresDob = !['ADT','ADULT'].includes(passengerType) || Boolean(doc.senior_citizen) || Boolean(p.senior_citizen);
      const checkEntries = Object.entries(session?.document_checks || {}).filter(([key]) => key.startsWith(`${p.passenger_key}#`));
      return <div className="panel" key={p.passenger_key}><div className="passenger-header"><div className="avatar">{(p.first_name?.[0] || 'P')}{(p.last_name?.[0] || '')}</div><div><span className="step-kicker">PASSENGER {idx + 1}</span><h2>{p.title} {p.first_name} {p.last_name}</h2><p>{p.passenger_key} · {p.senior_citizen ? 'Senior citizen' : p.passenger_type}</p></div><span className={`status-chip ${decisionClass(checkEntries[0]?.[1]?.decision || 'NOT_RUN')}`}>{checkEntries[0]?.[1]?.decision || 'NOT CHECKED'}</span></div>
        <div className="form-grid doc-grid">
          <label>Passport number <input value={doc.passport_number || ''} onChange={e => setDocField(p.passenger_key,'passport_number',e.target.value.toUpperCase().replace(/\s+/g, ''))} pattern="[A-Za-z0-9]{5,20}" title="Use 5–20 letters and digits only. Do not enter a real passport number." minLength={5} maxLength={20} required autoComplete="off" placeholder="Fictional passport ID"/></label>
          <CountryCodeInput id={`checkin-nationality-${p.passenger_key}`} label="Nationality / country" value={doc.nationality || ''} onChange={value => setDocField(p.passenger_key,'nationality',value)} apiBase={API} placeholder="India, IN or IND"/>
          <CountryCodeInput id={`checkin-issuing-country-${p.passenger_key}`} label="Issuing country" value={doc.issuing_country || ''} onChange={value => setDocField(p.passenger_key,'issuing_country',value)} apiBase={API} placeholder="United Arab Emirates, AE or ARE"/>
          <label>Date of birth <input type="date" min="1900-01-01" max={new Date().toISOString().slice(0,10)} value={doc.date_of_birth || ''} onChange={e => setDocField(p.passenger_key,'date_of_birth',e.target.value)} required={requiresDob}/><small>{requiresDob ? 'Required for this passenger category' : 'Optional for a regular adult; chatbot will ask and allow skip'}</small></label>
          <label>Passport expiry <input type="date" min={new Date().toISOString().slice(0,10)} value={doc.expiry_date || ''} onChange={e => setDocField(p.passenger_key,'expiry_date',e.target.value)} required/></label>
          <label>Purpose of stay <select required value={doc.purpose_of_stay || 'TOURISM'} onChange={e => setDocField(p.passenger_key,'purpose_of_stay',e.target.value)}><option value="TOURISM">Tourism / holiday</option><option value="BUSINESS">Business</option><option value="TRANSIT">Transit</option><option value="STUDY">Study</option><option value="WORK">Work / employment</option><option value="FAMILY">Visit family</option><option value="MEDICAL">Medical treatment</option></select></label><label>Duration of stay (days) <input required type="number" min={1} max={365} step={1} value={doc.duration_of_stay_days ?? 7} onChange={e => setDocField(p.passenger_key,'duration_of_stay_days',e.target.value === '' ? '' : Number(e.target.value))}/><small>Whole days, 1–365. Some purposes or longer stays require authoritative entry-permit review.</small></label>
        </div>
        {checkEntries.map(([key, result]) => <div className={`validation-result ${decisionClass(result.decision)}`} key={key}><strong>{result.decision} · {result.evaluation_code}</strong><ul>{(result.reasons || []).map(reason => <li key={reason}>{reason}</li>)}</ul><small>{result.sources?.map(s => s.title).join(' · ')}</small></div>)}
      </div>;
    })}
    {session?.eligibility?.rules && <div className="panel"><div className="section-title"><div><h2>Booking eligibility rules</h2><p>Reference-derived, codified rules. Emirates-specific policy approval is not implied.</p></div></div><div className="rule-list">{session.eligibility.rules.map(rule => <div className="rule-row" key={rule.rule_id}><span className={`rule-dot ${rule.decision === 'PASS' ? 'dot-good' : 'dot-bad'}`}></span><div><strong>{rule.name}</strong><p>{rule.message}</p></div><span className={`status-chip ${decisionClass(rule.decision)}`}>{rule.decision}</span></div>)}</div></div>}
    <div className="footer-actions"><button className="btn btn-quiet" onClick={() => setActiveStep('booking')}><ArrowLeft size={16}/> Back</button><button className="btn btn-primary" onClick={saveAndValidateDocuments} disabled={busy || passengers.some(p => { const d = documents[p.passenger_key] || {}; const type = String(p.passenger_type || 'ADT').toUpperCase(); const dobRequired = !['ADT','ADULT'].includes(type) || Boolean(d.senior_citizen) || Boolean(p.senior_citizen); return String(d.passport_number || '').trim().length < 5 || !/^[A-Z]{2,3}$/i.test(String(d.nationality || '').trim()) || !/^[A-Z]{2,3}$/i.test(String(d.issuing_country || '').trim()) || !d.expiry_date || !d.purpose_of_stay || !Number.isInteger(Number(d.duration_of_stay_days)) || Number(d.duration_of_stay_days) < 1 || Number(d.duration_of_stay_days) > 365 || (d.date_of_birth && d.date_of_birth > new Date().toISOString().slice(0,10)) || (dobRequired && !d.date_of_birth) || (p.senior_citizen && d.date_of_birth && (() => { const dep = new Date((session?.booking?.journeys?.[0]?.departure_date || new Date().toISOString().slice(0,10)) + 'T12:00:00'); const dob = new Date(d.date_of_birth + 'T12:00:00'); return dep.getFullYear() - dob.getFullYear() - ((dep.getMonth() < dob.getMonth() || (dep.getMonth() === dob.getMonth() && dep.getDate() < dob.getDate())) ? 1 : 0) < 60; })()); })}>{busy ? <RefreshCw className="spin" size={17}/> : <ShieldCheck size={17}/>} Save & validate documents <ArrowRight size={16}/></button></div>
  </div>;

  const skipSeatSelection = async () => withBusy(async () => {
    await api(`/api/v1/sessions/${sid}/seats/skip`, { method: 'POST' }, token);
    const updated = await refreshSession();
    setActiveStep('extras');
    setChatMessages(prev => [...prev, { role: 'assistant', text: 'Seat selection has been skipped for this synthetic journey. No seat was assigned. You can still choose optional extras and review the remaining required gates.', card: { type: 'seats', title: 'Seat selection', status: 'Skipped by passenger', note: 'This skips only seat choice. Eligibility, document validation and final confirmation remain mandatory.' } }]);
    setNotice({ kind: 'info', text: `Seat selection skipped. ${updated?.seat_selection_skipped ? 'Your choice is saved for this demo session.' : 'The workflow has continued.'}` });
  });

  const renderSeats = () => {
    const selected = session?.seat_assignments || {};
    const selectedJourney = journeys.find(j => j.journey_key === journeyForSeat) || firstJourney;
    const selectedAssignmentKey = passengerKey => `${passengerKey}#${selectedJourney.journey_key}`;
    const seatMap = seatInventory.length ? seatInventory : [];
    const rowNums = [...new Set(seatMap.map(s => Number(s.seat_code.match(/^\d+/)?.[0])).filter(Boolean))].sort((a,b)=>a-b);
    return <div className="page-stack">
      <div className="page-heading"><div><div className="eyebrow">STEP 03 · SEAT SELECTION</div><h1>Choose your seat</h1><p>Mock seat availability and assignment through the Navitaire adapter.</p></div><div className="hero-plane"><Plane size={25}/></div></div>
      <div className="panel"><div className="flight-summary"><div><span className="step-kicker">SYNTHETIC FLIGHT</span><h2>{selectedJourney.origin} <ArrowRight size={16}/> {selectedJourney.destination}</h2><p>Carrier and inventory are simulated · {fmtHours(selectedJourney.departure_offset_hours)} until departure</p></div><span className="status-chip status-good">Documents passed</span></div>
        <div className="checkin-passenger-tabs" role="group" aria-label="Select passenger for seat"><strong>Passenger</strong>{passengers.map((p, idx) => <button type="button" key={p.passenger_key} className={passengerForSeat === p.passenger_key ? 'active' : ''} onClick={() => setPassengerForSeat(p.passenger_key)}>{idx + 1}. {p.first_name}</button>)}</div>
        <label className="seat-passenger">Select flight segment<select value={selectedJourney.journey_key || ''} onChange={e => { setJourneyForSeat(e.target.value); loadSeatMap(e.target.value).catch(err => surfaceWorkflowError('seat inventory', err)); }}>{journeys.map(j => <option key={j.journey_key} value={j.journey_key}>{j.origin} → {j.destination} · {j.journey_key}</option>)}</select></label>
        <label className="seat-passenger">Select passenger<select value={passengerForSeat} onChange={e => setPassengerForSeat(e.target.value)}>{passengers.map(p => <option key={p.passenger_key} value={p.passenger_key}>{p.first_name} {p.last_name}</option>)}</select></label>
        <div className="seat-legend"><span><i className="seat-sample available"/> Available</span><span><i className="seat-sample occupied"/> Unavailable</span><span><i className="seat-sample selected"/> Selected</span></div>
        <div className="seat-map-wrap"><div className="seat-cabin"><div className="seat-cabin-nose"><Plane size={20}/><span>FRONT OF AIRCRAFT</span></div><div className="seat-map">{rowNums.map(row => <React.Fragment key={row}><span className="row-label">{row}</span>{['A','B','C','D','E','F'].map(letter => { const code = `${row}${letter}`; const info = seatMap.find(s => s.seat_code === code); const picked = selected[selectedAssignmentKey(passengerForSeat)]?.seat_code === code; return <button key={code} className={`seat ${picked?'seat-picked':info?.available?'seat-free':'seat-taken'} ${letter==='D'?'aisle-gap':''}`} disabled={!info?.available || busy} title={info?.available ? `Select ${code}` : `${code} unavailable`} onClick={() => selectSeat(code)}>{letter}</button>; })}</React.Fragment>)}</div><div className="seat-cabin-tail">EXIT · MOCK SEAT MAP</div></div></div>
        <div className="assigned-list">{passengers.map(p => <div className="assigned-row" key={p.passenger_key}><span>{p.first_name} {p.last_name}</span><strong>{selected[selectedAssignmentKey(p.passenger_key)]?.seat_code || 'Not selected'} · {selectedJourney.origin}–{selectedJourney.destination}</strong></div>)}</div>
        <div className="footer-actions"><button className="btn btn-quiet" onClick={() => setActiveStep('documents')}><ArrowLeft size={16}/> Documents</button><button className="btn btn-secondary" onClick={() => confirmSeatFor(passengerForSeat, selectedJourney.journey_key)} disabled={busy || !session?.seat_assignments?.[`${passengerForSeat}#${selectedJourney.journey_key}`]?.seat_code || confirmedSeatKeys.includes(`${passengerForSeat}#${selectedJourney.journey_key}`)}>Confirm seat & next passenger</button><button className="btn btn-quiet" onClick={() => skipPassengerSeat(passengerForSeat, selectedJourney.journey_key)} disabled={busy || !session || session.document_decision !== 'PASS' || session.eligibility?.decision !== 'PASS'}>Skip this passenger’s seat</button><button className="btn btn-quiet" onClick={skipSeatSelection} disabled={busy || !session || session.document_decision !== 'PASS' || session.eligibility?.decision !== 'PASS'}>Skip all seats</button><button className="btn btn-primary" disabled={!session?.seat_selection_skipped && (Object.keys(selected).length + (session?.seat_selection_skipped_assignments || []).length) < requiredSeatCount} onClick={() => setActiveStep('extras')}>Continue to extras <ArrowRight size={16}/></button></div>
      </div>
    </div>;
  };

  const renderExtras = () => <div className="page-stack">
    <div className="page-heading"><div><div className="eyebrow">STEP 04 · ANCILLARY SERVICES</div><h1>Make the journey yours</h1><p>Optional demo add-ons. No payment is collected and nothing is purchased.</p></div><div className="hero-plane"><ShoppingBag size={25}/></div></div>
    <div className="disclaimer"><Info size={17}/><span>Prices and availability are fictional demonstration values, not Emirates products, offers or prices.</span></div>
    <div className="checkin-passenger-tabs" role="group" aria-label="Select passenger for add-ons"><strong>Passenger</strong>{passengers.map((p, idx) => <button type="button" key={p.passenger_key} className={activeExtrasPassenger === idx ? 'active' : ''} onClick={() => setActiveExtrasPassenger(idx)}>{idx + 1}. {p.first_name}</button>)}</div>
    <div className="panel-inline-actions"><span>Passenger {Math.min(activeExtrasPassenger + 1, Math.max(passengers.length, 1))} of {passengers.length}: review one passenger at a time</span><span>{passengers.map((p, i) => `${i + 1}: ${(ancillarySelection[p.passenger_key] || []).length} add-on(s)`).join(' · ')}</span></div>
    {passengers.filter((_, i) => i === activeExtrasPassenger).map(p => <div className="panel" key={p.passenger_key}><div className="passenger-header"><div className="avatar">{p.first_name?.[0]}{p.last_name?.[0]}</div><div><h2>{p.first_name} {p.last_name}</h2><p>Passenger add-ons · {activeExtrasPassenger + 1} / {passengers.length}</p></div></div><div className="ancillary-grid">{ancillaryCatalog.map(item => { const checked = (ancillarySelection[p.passenger_key] || []).includes(item.code); return <label className={`ancillary-card ${checked?'ancillary-selected':''}`} key={item.code}><input type="checkbox" checked={checked} onChange={e => setAncillarySelection(prev => ({ ...prev, [p.passenger_key]: e.target.checked ? [...(prev[p.passenger_key] || []), item.code] : (prev[p.passenger_key] || []).filter(c => c !== item.code) }))}/><div className="ancillary-symbol">{item.code==='EXTRA_BAG'?'＋':item.code==='LOUNGE'?'◇':item.code==='WIFI'?'⌁':'☷'}</div><div className="ancillary-text"><strong>{item.label || item.name}</strong><span>{item.description || 'Mock optional service'}</span><b>{fmtMoney(item.price)}</b></div>{checked && <CheckCircle2 className="ancillary-check" size={18}/>}</label>; })}</div><div className="panel-inline-actions"><span>Passenger add-on total <strong>{fmtMoney((ancillarySelection[p.passenger_key] || []).reduce((sum, code) => sum + Number(ancillaryCatalog.find(x=>x.code===code)?.price || 0),0))}</strong></span><button className="btn btn-secondary btn-small" onClick={() => saveAncillaries(p,{advance:true})} disabled={busy}>{activeExtrasPassenger + 1 < passengers.length ? 'Confirm & next passenger' : 'Save last passenger'}</button><button className="btn btn-quiet btn-small" onClick={() => saveAncillaries(p,{advance:true,skip:true})} disabled={busy}>Skip this passenger</button></div></div>)}
    <div className="sticky-total"><div><span>All passenger add-ons</span><strong>{fmtMoney(ancillaryTotal)}</strong></div><button className="btn btn-primary" onClick={confirmExtras} disabled={busy}>{busy ? <RefreshCw className="spin" size={17}/> : <Check size={17}/>} Confirm extras <ArrowRight size={16}/></button></div>
  </div>;

  const renderReview = () => {
    const selections = session?.ancillary_selections || {};
    const allAddons = Object.entries(selections).flatMap(([paxKey, items]) => items.map(item => ({ ...item, label: item.label || item.name, paxKey })));
    const total = allAddons.reduce((sum, item) => sum + Number(item.price || 0), 0);
    return <div className="page-stack">
      <div className="page-heading"><div><div className="eyebrow">STEP 05 · FINAL REVIEW</div><h1>Review your check-in</h1><p>Confirm the synthetic data and workflow status before generating a mock boarding pass.</p></div><div className="hero-plane"><ClipboardCheck size={25}/></div></div>
      <div className="panel"><div className="section-title"><div><h2>Itinerary</h2><p>Mock airline record returned by the local Navitaire adapter.</p></div><span className="status-chip status-good">Booking validated</span></div><div className="journey-review-list">{journeys.map(j => <div className="route-card" key={j.journey_key}><div><small>FROM</small><strong>{j.origin}</strong></div><div className="route-line"><Plane size={17}/><span>{j.international ? 'INTERNATIONAL · MOCK' : 'DOMESTIC · MOCK'}</span></div><div><small>TO</small><strong>{j.destination}</strong></div></div>)}</div><div className="review-kv"><span>Booking reference</span><strong>{session?.booking?.record_locator}</strong><span>Passengers</span><strong>{passengers.length}</strong><span>Departure windows</span><strong>{journeys.map(j => `${j.origin}–${j.destination} ${fmtHours(j.departure_offset_hours)}`).join(' · ')}</strong><span>Provider status</span><strong>Mock Navitaire + Mock Timatic</strong></div></div>
      <div className="panel"><div className="section-title"><div><h2>Passenger and document checks</h2><p>Passport numbers are not shown in this review summary.</p></div><ShieldCheck className="section-icon" size={24}/></div>{passengers.map(p => { const checks=Object.entries(session?.document_checks||{}).filter(([k])=>k.startsWith(`${p.passenger_key}#`)).map(([,v])=>v); return <div className="review-row" key={p.passenger_key}><div><strong>{p.first_name} {p.last_name}</strong><p>{p.passenger_key} · Document checks {checks.map(c=>c.evaluation_code).join(', ') || 'not run'}</p></div><span className={`status-chip ${decisionClass(checks.every(c=>c.decision==='PASS')?'PASS':session?.document_decision || 'NOT_RUN')}`}>{checks.length && checks.every(c=>c.decision==='PASS') ? 'PASS' : session?.document_decision || 'NOT RUN'}</span></div>; })}<div className="rule-summary"><div className="rule-summary-item"><span>Booking eligibility</span><strong className={decisionClass(session?.eligibility?.decision)}>{session?.eligibility?.decision || 'NOT RUN'}</strong></div><div className="rule-summary-item"><span>Document validation</span><strong className={decisionClass(session?.document_decision)}>{session?.document_decision || 'NOT RUN'}</strong></div></div></div>
      <div className="panel"><div className="section-title"><div><h2>Seats and extras</h2><p>All prices are synthetic AED demonstration amounts.</p></div></div>{passengers.map(p => <div className="review-row" key={p.passenger_key}><div><strong>{p.first_name} {p.last_name}</strong><p>{journeys.map(j => `${j.origin}–${j.destination}: ${session?.seat_assignments?.[`${p.passenger_key}#${j.journey_key}`]?.seat_code || 'Not assigned'}`).join(' · ')}</p><p>{(selections[p.passenger_key] || []).map(i => i.label).join(', ') || 'No add-ons selected'}</p></div><strong>{fmtMoney((selections[p.passenger_key] || []).reduce((s,i)=>s+Number(i.price||0),0))}</strong></div>)}<div className="total-row"><span>Mock ancillary total</span><strong>{fmtMoney(total)}</strong></div></div>
      <div className="disclaimer"><AlertTriangle size={17}/><span>Submitting creates a simulated check-in record only. It does not contact Emirates, issue a valid travel document, or confirm real immigration eligibility.</span></div>
      <div className="footer-actions"><button className="btn btn-quiet" onClick={() => setActiveStep('extras')}><ArrowLeft size={16}/> Back to extras</button><button className="btn btn-primary" onClick={completeCheckin} disabled={busy || session?.eligibility?.decision !== 'PASS' || session?.document_decision !== 'PASS' || !session?.ancillary_stage_complete || (!session?.seat_selection_skipped && (Object.keys(session?.seat_assignments || {}).length + (session?.seat_selection_skipped_assignments || []).length) < requiredSeatCount)}>{busy ? <RefreshCw className="spin" size={17}/> : <LockKeyhole size={17}/>} Complete mock check-in <ArrowRight size={16}/></button></div>
    </div>;
  };

  const renderBoarding = () => <div className="page-stack boarding-page">
    <div className="success-hero"><div className="success-icon"><CheckCircle2 size={32}/></div><div><div className="eyebrow">DEMO COMPLETE</div><h1>You're checked in — in the mock environment</h1><p>This is a generated POC result, not a real boarding pass or authorization to travel.</p></div></div>
    {(session?.boarding_passes || []).map(pass => <div className="boarding-pass" key={pass.boarding_pass_id}><div className="bp-main"><div className="bp-brand"><span className="brand-mark">E</span><span>EMIRATES <small>CHECK-IN LAB · MOCK</small></span></div><div className="bp-route"><div><small>FROM</small><strong>{pass.origin}</strong><span>{pass.origin==='DEL'?'New Delhi':pass.origin==='DXB'?'Dubai':pass.origin}</span></div><div className="bp-route-line"><Plane size={20}/><span>EK · DEMO</span></div><div><small>TO</small><strong>{pass.destination}</strong><span>{pass.destination==='DXB'?'Dubai':pass.destination==='LHR'?'London Heathrow':pass.destination==='CDG'?'Paris Charles de Gaulle':pass.destination}</span></div></div><div className="bp-passenger"><small>PASSENGER</small><strong>{pass.passenger_name}</strong></div><div className="bp-manual-lookup"><small>MANUAL WEB CHECK-IN LOOKUP · DEMO</small><strong>PNR - {pass.record_locator || session?.booking?.record_locator || 'DEMO-PNR'}</strong><strong>Last Name - {pass.manual_checkin_last_name || (passengers.find(p => ['ADT','ADULT'].includes(String(p.passenger_type || '').toUpperCase())) || passengers[0] || {}).last_name || 'NOT_SET'}</strong></div><div className="bp-details"><div><small>SEAT</small><strong className="seat-large">{pass.seat}</strong></div><div><small>BOARDING GROUP</small><strong>{pass.boarding_group}</strong></div><div><small>DEPARTURE WINDOW</small><strong>{fmtHours(pass.departure_in_hours)}</strong></div><div><small>MOCK PASS ID</small><strong>{pass.boarding_pass_id}</strong></div></div></div><div className="bp-stub"><div className="mock-qr" aria-label="Decorative mock code"><div/><div/><div/><div/><span>DEMO</span></div><strong>NOT VALID FOR TRAVEL</strong><small>Decorative placeholder — not scannable</small></div></div>)}
    {!session?.boarding_passes?.length && <div className="panel empty-state"><Ticket size={30}/><h2>No boarding pass has been generated</h2><p>Complete all required gates from the previous workflow steps.</p><button className="btn btn-primary" onClick={() => setActiveStep('review')}>Return to review</button></div>}
    <div className="footer-actions"><button className="btn btn-quiet" onClick={() => setShowEvents(v=>!v)}>{showEvents?'Hide':'Show'} agent trace</button><button className="btn btn-secondary" onClick={() => window.print()}><ClipboardCheck size={16}/> Print demo result</button><button className="btn btn-primary" onClick={clearSession}>Start a new demo <RefreshCw size={16}/></button></div>
    {showEvents && <div className="panel"><h2>Redacted agent event trace</h2><p className="muted">No raw passport details, session tokens or prompts are shown here.</p><div className="event-list">{(session?.events || []).map(e=><div className="event-row" key={e.event_id}><time>{new Date(e.timestamp).toLocaleTimeString()}</time><strong>{e.component}</strong><span>{e.action}</span><span className={`status-chip ${decisionClass(e.outcome)}`}>{e.outcome}</span></div>)}</div></div>}
  </div>;

  const renderStep = () => {
    if (activeStep === 'booking') return renderBooking();
    if (activeStep === 'documents') return renderDocuments();
    if (activeStep === 'seats') return renderSeats();
    if (activeStep === 'extras') return renderExtras();
    if (activeStep === 'review') return renderReview();
    if (activeStep === 'boarding') return renderBoarding();
    return renderBooking();
  };

  return <div className="app-shell" onInvalidCapture={e => { const target = e.target; const field = target.closest('label')?.textContent?.trim() || target.getAttribute('aria-label') || target.name || target.placeholder || 'required field'; setError(`${field}: ${target.validationMessage || 'Please complete this field in the required format.'}`); setChatOpen(true); }}>
    <header className="topbar"><button className="brand-lockup" onClick={onExit} aria-label="Return to flight search home"><span className="brand-emblem"><img src="/emirates-logo-badge.svg" alt="Emirates" /></span><span className="brand-text"><strong>EMIRATES</strong><small>WEB CHECK-IN LAB</small></span></button><div className="topbar-center"><span className="topbar-status"><i/> SYNTHETIC ENVIRONMENT</span><span className="topbar-divider"/><span className="topbar-reference">OPEN-SOURCE MULTI-AGENT POC</span></div><div className="topbar-right"><button className="btn btn-outline btn-small exit-portal-button" onClick={onExit}>Back to flight website</button><span className="privacy-chip"><LockKeyhole size={13}/> Demo data only</span>{session && <button className="btn btn-outline btn-small" onClick={clearSession}>Reset session</button>}</div></header>
    <div className="unofficial-banner"><Info size={15}/><span>Unofficial Emirates-inspired proof of concept · Not connected to Emirates, Navitaire, Timatic or any live airline system</span></div>
    <div className="app-layout">
      <aside className="journey-sidebar"><div className="sidebar-title"><span>YOUR JOURNEY</span><span>{session ? 'IN PROGRESS' : 'READY'}</span></div><div className="step-list">{FLOW.map((step, i) => { const Icon=step.icon; const done = step.id==='boarding' ? Boolean(session?.checked_in) : step.id==='review' ? Boolean(session?.ancillary_stage_complete) : step.id==='extras' ? Boolean(session?.ancillary_stage_complete) : step.id==='seats' ? (Boolean(session?.seat_selection_skipped) || (Object.keys(session?.seat_assignments||{}).length + (session?.seat_selection_skipped_assignments||[]).length)>=requiredSeatCount) && passengers.length>0 : step.id==='documents' ? session?.document_decision==='PASS' : false; const isActive=activeStep===step.id; const allowed = step.id==='booking' || (session && (step.id==='documents' || (step.id==='seats' && session.document_decision==='PASS' && session.eligibility?.decision==='PASS') || (step.id==='extras' && session.document_decision==='PASS' && (session.seat_selection_skipped || Object.keys(session.seat_assignments||{}).length>=requiredSeatCount)) || (step.id==='review' && session.ancillary_stage_complete) || (step.id==='boarding' && session.checked_in))); return <button key={step.id} className={`step-nav ${isActive?'step-active':''} ${done?'step-done':''} ${!allowed?'step-locked':''}`} onClick={() => goTo(step.id)}><span className="step-icon"><Icon size={18}/></span><span className="step-label"><strong>{step.title}</strong><small>{step.label}</small></span><span className="step-indicator">{done?<Check size={15}/>:<span>{String(i+1).padStart(2,'0')}</span>}</span></button>; })}</div><div className="sidebar-bottom"><div className="secure-note"><ShieldCheck size={18}/><div><strong>Guardrails active</strong><span>Rules gate transactions. AI cannot override a failed check.</span></div></div><div className="sidebar-version">Local POC · v0.3.0</div></div></aside>
      <main className="main-content">{renderNotice()}{error && <div className="notice notice-error"><AlertTriangle size={17}/><span>{error}</span><button className="icon-button" onClick={() => setError('')} aria-label="Dismiss error"><X size={15}/></button></div>}{renderStep()}<footer className="page-footer"><span>© Synthetic demonstration only</span><span><LockKeyhole size={12}/> Privacy by design · no live customer data</span><button onClick={() => setShowEvents(v=>!v)} className="text-button">{showEvents?'Hide':'View'} system trace</button></footer>{showEvents && activeStep!=='boarding' && <div className="panel"><h3>Redacted agent trace</h3>{(session?.events || []).map(e=><div className="event-row" key={e.event_id}><time>{new Date(e.timestamp).toLocaleTimeString()}</time><strong>{e.component}</strong><span>{e.action}</span><span className={`status-chip ${decisionClass(e.outcome)}`}>{e.outcome}</span></div>)}</div>}</main>
      <aside className={`chat-sidebar ${chatOpen?'chat-open':'chat-closed'}`}><div className="chat-header"><div className="chat-avatar"><Plane size={19}/></div><div className="chat-title"><strong>Check-in assistant</strong><span><i/> {(FLOW.find(step => step.id === activeStep)?.title || 'Booking lookup')} · shared page/chat context</span></div><button className="icon-button" onClick={() => setChatOpen(v=>!v)} aria-label={chatOpen?'Collapse chat':'Expand chat'}>{chatOpen?<X size={18}/>:<MessageCircle size={18}/>}</button></div>{chatOpen && <><div className="chat-scope"><ShieldCheck size={14}/><span>Current page context is shared with the assistant. All actions are validated by the same mock workflow gates.</span></div><div className="chat-messages">{chatMessages.map((m,i)=><div className={`chat-message ${m.role}`} key={`${m.role}-${i}`}><div className="message-avatar">{m.role==='user'?'You':'E'}</div><div className="message-body"><p style={{ whiteSpace: 'pre-wrap' }}>{m.text}</p><ConversationCard card={m.card}/>{m.citations?.length>0 && <div className="citation-list">{m.citations.map(c=>c.url?.startsWith('https://')?<a key={c.url||c.title} href={c.url} target="_blank" rel="noreferrer">{c.title || c.url} ↗</a>:<span key={c.title}>{c.title || 'Demo policy'} · local demo source</span>)}</div>}</div></div>)}{chatBusy && <div className="typing"><span/><span/><span/> checking the workflow</div>}</div>{['nationality','issuing_country'].includes(chatPendingField) && <div className="chat-country-picker"><strong>Search all countries by name or ISO code</strong><CountryCodeInput id="checkin-chat-country-answer" label="" value={countryAnswer} onChange={setCountryAnswer} apiBase={API} placeholder="Country name, ISO-2 or ISO-3"/><button type="button" disabled={chatBusy || !countryAnswer.trim()} onClick={() => sendChat(null,countryAnswer)}>Use country <ArrowRight size={13}/></button><small>The selected country's full name is shown here; its ISO code is sent to validation.</small></div>}{({confirm_section_edit:['Yes','No'],confirm_pdp_edit:['Yes','No'],title:['MR','MRS','MS','MISS','MSTR','DR'],trip_type:['One way','Round trip'],cabin:['Economy','Premium Economy','Business Class','First Class'],gender:['Male','Female','X','Unspecified'],purpose_of_stay:['Tourism','Business','Transit','Study','Work','Family','Medical'],duration_of_stay_days:['7 days','14 days','30 days','90 days']})[chatPendingField] && <div className="chat-option-card"><strong>Choose {chatPendingField.replaceAll('_',' ')}</strong><div className="chat-suggestions">{({confirm_section_edit:['Yes','No'],confirm_pdp_edit:['Yes','No'],title:['MR','MRS','MS','MISS','MSTR','DR'],trip_type:['One way','Round trip'],cabin:['Economy','Premium Economy','Business Class','First Class'],gender:['Male','Female','X','Unspecified'],purpose_of_stay:['Tourism','Business','Transit','Study','Work','Family','Medical'],duration_of_stay_days:['7 days','14 days','30 days','90 days']})[chatPendingField].map(choice => <button type="button" key={choice} onClick={() => sendChat(null,choice)} disabled={chatBusy}>{choice}</button>)}</div><small>You can also type or speak your own valid value.</small></div>}<div className="chat-field-controls">{chatPendingField && !['record_locator','surname','checkin_eligibility','checkin_confirmation'].includes(chatPendingField) && <button type="button" onClick={() => (!window.confirm(`Keep the current ${String(chatPendingField).replaceAll('_', ' ')} value and move to the next field?`) ? null : sendChat(null,'skip'))} disabled={chatBusy}><SkipForward size={13}/> Skip / keep current value</button>}{sid && activeStep==='documents' && <button type="button" onClick={() => sendChat(null,'modify document details')} disabled={chatBusy}><Pencil size={13}/> Modify / update document fields</button>}</div><div className="chat-flow-actions">{currentStepIndex > 0 && <button type="button" onClick={() => goTo(FLOW[currentStepIndex - 1].id)}><ArrowLeft size={14}/> Back</button>}{currentStepIndex >= 0 && currentStepIndex < FLOW.length - 1 && <button type="button" onClick={() => goTo(FLOW[currentStepIndex + 1].id)}><ArrowRight size={14}/> Next page</button>}</div><div className="chat-suggestions"><button onClick={() => sendChat(null,sid?'check my eligibility':'lookup EM8K2Q THOMAS')} disabled={chatBusy}>{sid?'Check eligibility':'Load demo booking'}</button><button onClick={() => sendChat(null,'What is the online check-in window?')} disabled={chatBusy}>Check-in FAQ</button><button onClick={() => sendChat(null,'show seats')} disabled={!sid || chatBusy}>Show seats</button></div><form onSubmit={sendChat} className="chat-composer"><textarea value={chatText} onChange={e => { interactionTurnRef.current += 1; stopSpeaking(); cancelVoiceInput(); speakNextResponseRef.current = false; setChatText(e.target.value); }} onKeyDown={e => { if (e.key==='Enter'&&!e.shiftKey) {e.preventDefault();sendChat(e);} }} rows={2} maxLength={1000} placeholder={recording ? "Listening…" : transcribing ? "Recognizing speech…" : "Ask a flight-related question…"}/><button type="button" className={recording ? "voice-recording" : ""} disabled={!speechAvailable || chatBusy} onClick={startVoiceInput} aria-label="Speak to assistant" title="Speak to assistant"><Mic size={16}/></button><button type="button" onClick={() => speak([...chatMessages].reverse().find(m => m.role === "assistant")?.text)} aria-label="Read last answer aloud" title="Read last answer aloud"><Volume2 size={16}/></button><button type="submit" disabled={chatBusy || !chatText.trim()} aria-label="Send message"><Send size={17}/></button><small>Enter to send · Shift+Enter for new line</small></form></>}</aside>
    </div>
    <div className="mobile-chat-launch"><button className="btn btn-primary" onClick={() => setChatOpen(v=>!v)}><MessageCircle size={17}/>{chatOpen?'Hide assistant':'Ask assistant'}</button></div>
  </div>;
}

export default CheckInWorkspace;
