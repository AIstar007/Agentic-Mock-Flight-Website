import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { speechPromptForCurrentTurn, useSarvamVoice } from './sarvamVoice.js';
import CountryCodeInput from './CountryCodeInput.jsx';
import {
  ArrowLeft, ArrowRight, Bell, BriefcaseBusiness, CalendarDays, Check, CheckCircle2,
  ChevronDown, ChevronRight, Clock3, Compass, CreditCard, HelpCircle, Luggage,
  MapPin, Menu, MessageCircle, Pencil, Plane, Search, Send, ShieldCheck, SkipForward, Sparkles,
  Ticket, UserRound, Wifi, X, Mic, Volume2
} from 'lucide-react';

const API = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const extraCatalog = [
  { code: 'EXTRA_BAG', title: 'Additional baggage', detail: 'Add an extra 10 kg to each passenger', price: 180, icon: Luggage },
  { code: 'LOUNGE', title: 'Lounge access', detail: 'Relax before your flight (demo)', price: 250, icon: BriefcaseBusiness },
  { code: 'WIFI', title: 'Onboard Wi-Fi', detail: 'Stay connected in the air (demo)', price: 65, icon: Wifi },
  { code: 'MEAL', title: 'Meal preference', detail: 'Record a preferred meal (no charge)', price: 0, icon: Compass },
];
const farePerks = {
  ECONOMY_SPECIAL: ['Cabin baggage', 'Standard check-in'],
  ECONOMY_SAVER: ['Cabin baggage', 'Changes may incur fees'],
  ECONOMY_FLEX: ['More flexibility', 'Standard seat options'],
  PREMIUM_ECONOMY: ['More space', 'Enhanced meal service'],
  BUSINESS: ['Business cabin', 'Lounge options'],
  FIRST: ['First cabin', 'Premium service'],
};

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    cache: 'no-store',
  });
  let data;
  try { data = await response.json(); } catch { data = { detail: `Request failed (${response.status})` }; }
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(typeof detail === 'string' ? detail : (typeof detail === 'object' ? JSON.stringify(detail) : `Request failed (${response.status})`));
  }
  return data;
}
function ConversationCard({ card }) {
  if (!card) return null;
  return <section className={`assistant-result-card assistant-result-${String(card.type || 'workflow').replace(/[^a-z0-9_-]/gi, '')}`}>
    <div className="assistant-result-heading"><strong>{card.title || 'Workflow summary'}</strong>{card.status && <span>{card.status}</span>}</div>
    {Array.isArray(card.fields) && card.fields.length > 0 && <dl>{card.fields.map((field, index) => <div key={`${field.label || 'field'}-${index}`}><dt>{field.label || 'Detail'}</dt><dd>{field.value == null || field.value === '' ? '—' : String(field.value)}</dd></div>)}</dl>}
    {card.note && <p>{card.note}</p>}
  </section>;
}

function ageOnDate(dobValue, onValue) {
  if (!dobValue || !onValue) return null;
  const dob = new Date(`${dobValue}T12:00:00`);
  const day = new Date(`${onValue}T12:00:00`);
  if (Number.isNaN(dob.getTime()) || Number.isNaN(day.getTime())) return null;
  return day.getFullYear() - dob.getFullYear() - ((day.getMonth() < dob.getMonth() || (day.getMonth() === dob.getMonth() && day.getDate() < dob.getDate())) ? 1 : 0);
}
function yearOffsetIso(value, years) {
  const date = new Date(`${value || localIsoDate()}T12:00:00`);
  date.setFullYear(date.getFullYear() + years);
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
}

function localIsoDate(days = 0) {
  const d = new Date(); d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function money(value, currency = 'AED') { return `${currency} ${Number(value || 0).toLocaleString('en-AE', { maximumFractionDigits: 0 })}`; }
function airportLabel(a) { return a ? `${a.city} (${a.iata})` : ''; }
function niceDate(value, options = { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }) {
  if (!value) return '—';
  const d = new Date(`${value}T12:00:00`);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString('en-GB', options);
}
function initials(value) { return String(value || 'E').trim().charAt(0).toUpperCase() || 'E'; }

function AirportField({ label, value, selected, onChange, onSelect, placeholder, required = true }) {
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let ignore = false;
    if (!value.trim() || selected) { setResults([]); return undefined; }
    const timer = setTimeout(async () => {
      setLoading(true);
      try { const data = await api(`/api/v1/airports/search?q=${encodeURIComponent(value)}&limit=6`); if (!ignore) setResults(data.results || []); }
      catch { if (!ignore) setResults([]); }
      finally { if (!ignore) setLoading(false); }
    }, 180);
    return () => { ignore = true; clearTimeout(timer); };
  }, [value, selected]);
  return <div className="portal-airport-field">
    <label>{label}</label>
    <div className={`portal-airport-input ${open ? 'is-open' : ''}`}>
      <MapPin size={18} />
      <input required={required} value={value} placeholder={placeholder} onFocus={() => setOpen(true)} onChange={e => { onChange(e.target.value); onSelect(null); setOpen(true); }} onBlur={() => setTimeout(() => setOpen(false), 150)} autoComplete="off" />
      {selected && <span className="portal-iata-pill">{selected.iata}</span>}
    </div>
    {open && !selected && value.trim() && <div className="portal-airport-suggestions">
      {loading && <div className="portal-suggestion muted">Searching airports…</div>}
      {!loading && results.map(a => <button type="button" key={a.iata} className="portal-suggestion" onMouseDown={e => e.preventDefault()} onClick={() => { onSelect(a); onChange(`${a.city}, ${a.country}`); setOpen(false); }}><span className="portal-suggestion-code">{a.iata}</span><span className="portal-suggestion-copy"><strong>{a.city}</strong><small>{a.airport} · {a.country}</small></span></button>)}
      {!loading && results.length === 0 && <div className="portal-suggestion muted">No matches. Try a city or airport code.</div>}
    </div>}
  </div>;
}

function SiteAssistant({ page, searchForm, setSearchForm, pdpContext, pageContext, pageError, onNavigate, onAssistantAction, searchResult }) {
  const pageLabel = ({ search: 'flight search', book: 'flight search', outbound: 'departure selection', return: 'return selection', passengers: 'passenger details', extras: 'optional extras', seats: 'seat selection', payment: 'payment simulation', confirmation: 'booking confirmation', manage: 'manage booking', checkin: 'web check-in', help: 'flight help' })[page] || 'flight assistance';
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([{ role: 'assistant', text: 'Hello. I can guide flight search, passenger details, synthetic booking steps, check-in and flight FAQs. This demo uses synthetic fares and bookings only.' }]);
  const [text, setText] = useState('');
  const [countryAnswer, setCountryAnswer] = useState('');
  const [busy, setBusy] = useState(false);
  const [assistantTask, setAssistantTask] = useState(() => { try { return sessionStorage.getItem('emirates_demo_assistant_task') || null; } catch { return null; } });
  const [pendingField, setPendingField] = useState(() => { try { return sessionStorage.getItem('emirates_demo_pending_field') || null; } catch { return null; } });
  const [pendingPassengerIndex, setPendingPassengerIndex] = useState(() => { try { const v=sessionStorage.getItem('emirates_demo_pending_passenger'); return v === null ? null : Number(v); } catch { return null; } });
  const [assistantValues, setAssistantValues] = useState(() => { try { return JSON.parse(sessionStorage.getItem('emirates_demo_assistant_values') || '{}'); } catch { return {}; } });
  const [pendingFlightIndex, setPendingFlightIndex] = useState(() => { try { const v=sessionStorage.getItem('emirates_demo_pending_flight'); return v === null ? null : Number(v); } catch { return null; } });
  const speakNextResponseRef = useRef(false);
  const voiceFollowupRef = useRef(false);
  const interactionTurnRef = useRef(0);
  const lastReportedError = useRef('');
  const voice = useSarvamVoice({
    apiBase: API,
    onTranscript: (transcript) => { voiceFollowupRef.current = true; setText(transcript); speakNextResponseRef.current = true; submit(null, transcript); },
    onError: message => setMessages(old => [...old, { role: 'assistant', text: message }]),
  });
  const { recording, transcribing, startVoice, cancelVoiceInput, speak, stopSpeaking } = voice;
  const startVoiceInput = () => { voiceFollowupRef.current = true; interactionTurnRef.current += 1; stopSpeaking(); speakNextResponseRef.current = false; startVoice(); };
  const speechAvailable = voice.supported;
  useEffect(() => { setCountryAnswer(''); }, [pendingField]);
  useEffect(() => { try { assistantTask ? sessionStorage.setItem('emirates_demo_assistant_task', assistantTask) : sessionStorage.removeItem('emirates_demo_assistant_task'); pendingField ? sessionStorage.setItem('emirates_demo_pending_field', pendingField) : sessionStorage.removeItem('emirates_demo_pending_field'); pendingPassengerIndex == null ? sessionStorage.removeItem('emirates_demo_pending_passenger') : sessionStorage.setItem('emirates_demo_pending_passenger', String(pendingPassengerIndex)); sessionStorage.setItem('emirates_demo_assistant_values', JSON.stringify(assistantValues || {})); pendingFlightIndex == null ? sessionStorage.removeItem('emirates_demo_pending_flight') : sessionStorage.setItem('emirates_demo_pending_flight', String(pendingFlightIndex)); } catch {} }, [assistantTask, pendingField, pendingPassengerIndex, assistantValues, pendingFlightIndex]);
  useEffect(() => { setMessages(old => [...old, { role: 'assistant', text: `Page context updated: ${pageLabel}. I can help with this step and apply supported actions to the same demo workflow.` }]); }, [page]);
  useEffect(() => {
    if (!pageError) { lastReportedError.current = ''; return; }
    if (pageError === lastReportedError.current) return;
    lastReportedError.current = pageError; setOpen(true);
    const lower = String(pageError).toLowerCase();
    let reason = 'The page action did not complete or was rejected by a workflow validator.';
    let next = 'Review the visible fields and current step, resolve the validation message, then retry. Do not bypass a booking or check-in gate.';
    if (/airport|origin|destination|route/.test(lower)) { reason = 'The route fields are incomplete or an airport was not selected from the airport catalogue.'; next = 'Select both origin and destination from the suggestions and ensure they are different.'; }
    else if (/date|departure|return/.test(lower)) { reason = 'The date is missing, invalid, or inconsistent with the selected trip type.'; next = 'Choose a valid departure date and, for a return journey, a return date on or after departure.'; }
    else if (/required|terms|passenger|email|phone|name/.test(lower)) { reason = 'One or more required passenger or contact fields did not pass form validation.'; next = 'Complete highlighted fields, use names as shown on travel documents, enter valid contact details, and accept the demo terms.'; }
    else if (/declin|payment/.test(lower)) { reason = 'The synthetic payment outcome was declined; no charge was made and no booking should be assumed confirmed.'; next = 'Return to payment, select the successful simulation, review the total, and explicitly confirm the demo booking.'; }
    const explanation = `I couldn't complete the ${pageLabel} step.

What happened: ${reason}

What to do next: ${next}

Technical detail: ${String(pageError).slice(0, 320)}`;
    setMessages(old => [...old, { role: 'assistant', text: explanation }]);
  }, [pageError, pageLabel]);

  const visibleFlights = page === 'return' ? (searchResult?.return_flights || []) : (page === 'outbound' ? (searchResult?.outbound_flights || []) : []);
  const flightOptions = visibleFlights.map(f => ({ ...f, fare_options: (f.fare_options || []).filter(fare => fare.cabin === searchForm.cabin) }));
  const airportValue = (field, airport) => {
    if (!airport) return;
    setSearchForm(old => ({
      ...old,
      [field]: airport,
      [field + 'Text']: airportLabel(airport),
    }));
  };
  const applySearchField = async (action) => {
    const field = action.field;
    if (field === 'origin' || field === 'destination') {
      let airport = action.value;
      if (typeof airport === 'string') {
        const response = await api('/api/v1/airports/search?q=' + encodeURIComponent(airport) + '&limit=6');
        airport = (response.results || []).find(a => a.iata === action.value) || response.results?.[0];
      }
      if (airport) airportValue(field, airport);
    } else {
      setSearchForm(old => ({ ...old, [field]: action.value, ...(field === 'trip_type' && action.value === 'one_way' ? { return_date: '' } : {}), ...(['adults','children','infants','senior_citizens'].includes(field) ? { [field]: Number(action.value || 0) } : {}) }));
    }
  };
  const submit = async (event, overrideMessage = null) => {
    event?.preventDefault();
    const message = String(overrideMessage ?? text).trim();
    if (!message || busy) return;
    const submittedTurn = ++interactionTurnRef.current;
    const shouldSpeakResponse = speakNextResponseRef.current || voiceFollowupRef.current;
    stopSpeaking();
    setText('');
    setMessages(old => [...old, { role: 'user', text: message }]);
    setBusy(true);
    try {
      const workflow = {
        origin: searchForm.origin?.iata || null,
        destination: searchForm.destination?.iata || null,
        departure_date: searchForm.departure_date,
        return_date: searchForm.return_date,
        trip_type: searchForm.trip_type,
        adults: Number(searchForm.adults),
        children: Number(searchForm.children),
        infants: Number(searchForm.infants),
        senior_citizens: Number(searchForm.senior_citizens || 0),
        cabin: searchForm.cabin,
      };
      const context = {
        flight_search: { origin: workflow.origin, destination: workflow.destination },
        workflow,
        assistant_values: assistantValues,
        assistant_task: assistantTask,
        pending_field: pendingField,
        pending_passenger_index: pendingPassengerIndex,
        pending_flight_index: pendingFlightIndex,
        flight_leg: page,
        flight_options: flightOptions,
        pdp: pdpContext, page_context: pageContext || {}, page_error: pageError || null,
      };
      const result = await api('/api/v1/site-assistant/chat', { method: 'POST', body: JSON.stringify({ message, page, context }) });
      let card = result.card || null;
      if (!card && (result.actions || []).some(action => action.type === 'SET_SEARCH_FORM_FIELD')) {
        const fields = (result.actions || []).filter(action => action.type === 'SET_SEARCH_FORM_FIELD').map(action => ({ label: String(action.field || '').replaceAll('_', ' '), value: action.value ?? '—' }));
        card = { type: 'booking', title: 'Booking details updated', status: 'Draft · not booked', fields, note: 'These values were applied to the visible form. Flight search and booking are separate steps.' };
      } else if (!card && result.intent === 'slot_prompt') {
        const field = result.pending_field;
        const visible = field ? workflow[field] : null;
        card = { type: assistantTask === 'passenger_details' ? 'passenger' : 'booking', title: 'Next required detail', status: 'Waiting for your answer', fields: field ? [{ label: field.replaceAll('_', ' '), value: visible || 'Not provided' }] : [], note: 'Reply in one line. You can ask an FAQ without losing this step.' };
      } else if (!card && result.intent === 'flight_selected') {
        card = { type: 'flight', title: 'Flight selection', status: 'Selected · synthetic', fields: [{ label: 'Page', value: page }, { label: 'Flight ID', value: (result.actions || []).find(a => a.type === 'SELECT_FLIGHT')?.flight_id || 'Selected option' }, { label: 'Fare', value: (result.actions || []).find(a => a.type === 'SELECT_FLIGHT')?.fare_code || 'See itinerary' }], note: 'Review the itinerary on the page before continuing.' };
      } else if (!card && result.intent === 'faq') {
        card = { type: 'faq', title: 'Flight information', status: result.citations?.length ? 'Sources available' : 'Evidence-limited', note: 'FAQ responses do not alter the current booking workflow.' };
      }
      setMessages(old => [...old, { role: 'assistant', text: result.answer || 'I could not find an answer in the available sources.', citations: result.citations || [], card }]);
      if (shouldSpeakResponse && (speakNextResponseRef.current || voiceFollowupRef.current) && submittedTurn === interactionTurnRef.current && result.answer) {
        speak(speechPromptForCurrentTurn(result.answer, result.pending_field));
      }
      if (shouldSpeakResponse) speakNextResponseRef.current = false;
      if (Object.prototype.hasOwnProperty.call(result, 'assistant_task')) setAssistantTask(result.assistant_task);
      if (Object.prototype.hasOwnProperty.call(result, 'pending_field')) setPendingField(result.pending_field);
      if (Object.prototype.hasOwnProperty.call(result, 'pending_passenger_index')) setPendingPassengerIndex(result.pending_passenger_index);
      if (Object.prototype.hasOwnProperty.call(result, 'assistant_values')) setAssistantValues(result.assistant_values || {});
      if (Object.prototype.hasOwnProperty.call(result, 'pending_flight_index')) setPendingFlightIndex(result.pending_flight_index);
      for (const action of result.actions || []) {
        if (action.type === 'FILL_FLIGHT_SEARCH') {
          try {
            const responses = await Promise.all([
              api('/api/v1/airports/search?q=' + encodeURIComponent(action.origin) + '&limit=6'),
              api('/api/v1/airports/search?q=' + encodeURIComponent(action.destination) + '&limit=6'),
            ]);
            const origin = (responses[0].results || []).find(a => a.iata === action.origin) || responses[0].results?.[0];
            const destination = (responses[1].results || []).find(a => a.iata === action.destination) || responses[1].results?.[0];
            if (origin) airportValue('origin', origin);
            if (destination) airportValue('destination', destination);
            if (!['search', 'outbound', 'return'].includes(page)) onNavigate('book');
            if (onAssistantAction) await onAssistantAction({ type: 'NAVIGATE_FLOW_STEP', target: 'search' });
          } catch { /* Keep the assistant's response visible; a passenger can retry. */ }
        } else if (action.type === 'SET_SEARCH_FORM_FIELD') {
          await applySearchField(action);
          if (!['search', 'outbound', 'return'].includes(page)) { if (onAssistantAction) await onAssistantAction({ type: 'NAVIGATE_FLOW_STEP', target: 'search' }); onNavigate('book'); }
        } else if (action.type === 'NAVIGATE') {
          const target = action.target || 'manage';
          onNavigate(target);
        } else if (onAssistantAction) {
          await onAssistantAction(action);
          if (action.type === 'RUN_FLIGHT_SEARCH' || action.type === 'SELECT_FLIGHT' || action.type === 'CONFIRM_DEMO_BOOKING') {
            setAssistantTask(null);
            setPendingField(null);
            setAssistantValues({});
            setPendingFlightIndex(null);
          }
        }
      }
    } catch (error) {
      const explanation = `I couldn't complete the ${pageLabel} action.\n\nWhat happened: ${error.message || 'The request failed before returning a result.'}\n\nWhat to do next: Check visible validation, confirm earlier steps are complete, and retry. If it persists, inspect backend health and logs.`;
      setMessages(old => [...old, { role: 'assistant', text: explanation }]);
      if (shouldSpeakResponse && submittedTurn === interactionTurnRef.current) speak(speechPromptForCurrentTurn(explanation, pendingField));
      if (shouldSpeakResponse) speakNextResponseRef.current = false;
    } finally {
      setBusy(false);
    }
  };
  const suggestions = page === 'book' || page === 'search'
    ? ['Book a flight by chat', 'Fill passenger details by chat', 'Find flights from Delhi to Dubai']
    : page === 'passengers'
      ? ['Fill passenger details by chat', 'What name format should I use?', 'Can I add baggage later?']
      : page === 'outbound' || page === 'return'
        ? ['Choose flight 1', 'Choose flight 2', 'What is included in Economy?']
        : page === 'extras'
          ? ['Add extra baggage', 'Add Wi-Fi', 'Continue to seat selection']
          : page === 'payment'
            ? ['Choose UPI', 'Choose card payment', 'Simulate payment success', 'Confirm demo booking']
            : page === 'manage'
              ? ['Retrieve my booking', 'Open online check-in', 'How can I change my flight?']
              : ['How can I manage my booking?', 'What is the check-in window?', 'What documents may I need?'];
  const departureBase = new Date(`${searchForm?.departure_date || localIsoDate(1)}T12:00:00`);
  const futureReturn = days => { const day = new Date(departureBase); day.setDate(day.getDate() + days); return `${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,'0')}-${String(day.getDate()).padStart(2,'0')}`; };
  const flowChoices = {
    confirm_booking_edit: ['Yes', 'No'], confirm_pdp_edit: ['Yes', 'No'], confirm_section_edit: ['Yes', 'No'],
    title: ['MR', 'MRS', 'MS', 'MISS', 'MSTR', 'DR'], origin: ['Delhi', 'Mumbai', 'Dubai', 'Abu Dhabi', 'Singapore', 'London'], destination: ['Dubai', 'Delhi', 'Mumbai', 'Abu Dhabi', 'Singapore', 'London'], departure_date: [localIsoDate(1), localIsoDate(7), localIsoDate(14)], return_date: [futureReturn(7), futureReturn(14), futureReturn(21)], trip_type: ['One way', 'Round trip'], adults: ['1', '2', '3', '4', 'Skip'], children: ['0', '1', '2', '3', 'Skip'],
    infants: ['0', '1', '2', 'Skip'], senior_citizens: ['0', '1', '2', 'Skip'],
    cabin: ['Economy', 'Premium Economy', 'Business Class', 'First Class'], gender: ['Male', 'Female', 'X / unspecified', 'Prefer not to say'],
    purpose_of_stay: ['Tourism', 'Business', 'Transit', 'Study', 'Work', 'Family', 'Medical'], accepted_terms: ['Yes, I accept the demo terms', 'No'],
  };
  const adultCompanionChoices = pendingField === 'accompanying_adult_index'
    ? (pdpContext?.passengers || []).map((passenger, passengerIndex) => ({ passenger, passengerIndex }))
      .filter(({ passenger, passengerIndex }) => passengerIndex !== Number(pendingPassengerIndex) && passenger.passenger_type === 'ADT' && ageOnDate(passenger.date_of_birth, searchForm?.departure_date) >= 16)
      .map(({ passenger, passengerIndex }) => `Passenger ${passengerIndex + 1} · ${`${passenger.first_name || passenger.title || 'Adult'} ${passenger.last_name || ''}`.trim()}`)
    : [];
  const inlineChoices = pendingField === 'accompanying_adult_index' ? adultCompanionChoices : (flowChoices[pendingField] || []);
  const chatFlowSteps = ['search', 'outbound', ...(pageContext?.is_round_trip || searchForm?.trip_type === 'round_trip' ? ['return'] : []), 'passengers', 'extras', 'seats', 'payment', 'confirmation'];
  const flowIndex = chatFlowSteps.indexOf(page);
  const flowBackAvailable = flowIndex > 0 && flowIndex < chatFlowSteps.length;
  const flowNextAvailable = flowIndex >= 0 && flowIndex < chatFlowSteps.length - 1;
  if (!open) return <button type="button" className="portal-assistant-launcher" onClick={() => setOpen(true)} aria-label="Open Emirates flight assistant" title="Emirates flight assistant"><Plane size={25} strokeWidth={2.4}/><span/></button>;
  return <aside className="portal-assistant portal-assistant-open">
    <div className="portal-assistant-header"><span className="portal-assistant-orb"><Plane size={20}/></span><div><strong>Emirates travel assistant</strong><small><i/> {pageLabel} · shared page/chat state</small></div><button type="button" className="portal-icon-btn" onClick={() => setOpen(false)} aria-label="Minimize assistant"><X size={17}/></button></div>
    <>
      <div className="portal-assistant-scope"><ShieldCheck size={14}/> Page and chat share the same demo workflow. Reservations and payments are simulated; no money moves.</div>
      <div className="portal-assistant-messages">{messages.map((m, i) => <div className={'portal-chat-message ' + m.role} key={m.role + '-' + i}><span className="portal-chat-avatar">{m.role === 'user' ? 'Y' : 'E'}</span><div><p style={{ whiteSpace: 'pre-wrap' }}>{m.text}</p><ConversationCard card={m.card}/>{m.citations?.length > 0 && <div className="portal-chat-citations">{m.citations.map(c => c.url?.startsWith('https://') ? <a key={c.url || c.title} href={c.url} target="_blank" rel="noreferrer">{c.title || 'Read source'} ↗</a> : <span key={c.title} className="portal-chat-source-note">{c.title || 'Demo policy'} · local demo source</span>)}</div>}</div></div>)}{busy && <div className="portal-chat-loading">Checking schema and approved sources…</div>}</div>
      {inlineChoices.length > 0 && pendingField && <div className="portal-chat-option-card"><strong>{pendingField.startsWith('confirm_') ? 'Please confirm this change' : `Choose ${pendingField.replaceAll('_', ' ')}`}</strong><div className="portal-chat-prompts">{inlineChoices.map(item => <button key={item} type="button" onClick={() => submit(null, item)} disabled={busy}>{item}</button>)}</div><small>You can also type or speak a different valid value.</small></div>}
      {assistantTask && pendingField && !String(pendingField).startsWith('confirm_') && <div className="portal-chat-field-controls"><button type="button" onClick={() => (!window.confirm(`Keep the current ${String(pendingField).replaceAll('_', ' ')} value and move to the next field?`) ? null : submit(null, 'skip'))} disabled={busy}><SkipForward size={13}/> Skip / keep current value</button></div>}
      {(!assistantTask || !pendingField) && ['search','book','passengers','outbound','return','extras','seats','payment'].includes(page) && <div className="portal-chat-field-controls"><button type="button" onClick={() => submit(null, ['passengers'].includes(page) ? 'modify passenger details' : 'modify flight search')} disabled={busy}><Pencil size={13}/> Modify / update this section</button></div>}
      <div className="portal-chat-flow-actions">{flowBackAvailable && <button type="button" onClick={() => onAssistantAction?.({ type: 'BACK_MAIN_FLOW', page })}><ArrowLeft size={14}/> Back</button>}{flowNextAvailable && <button type="button" onClick={() => onAssistantAction?.({ type: 'ADVANCE_MAIN_FLOW', page })} disabled={busy}>Next <ArrowRight size={14}/></button>}</div>
      <div className="portal-chat-prompts">{suggestions.map(item => <button key={item} type="button" onClick={() => submit(null, item)} disabled={busy}>{item}</button>)}</div>
      {['nationality','issuing_country'].includes(pendingField) && assistantTask === 'passenger_details' && <div className="portal-chat-country-picker"><strong>Search all countries by name or ISO code</strong><CountryCodeInput id="booking-chat-country-answer" label="" value={countryAnswer} onChange={setCountryAnswer} apiBase={API} placeholder="Country name, ISO-2 or ISO-3"/><button type="button" disabled={busy || !countryAnswer.trim()} onClick={() => submit(null, countryAnswer)}>Use country <ArrowRight size={13}/></button><small>The selected country's full name is shown here; its ISO code is sent to the validator.</small></div>}
      <form className="portal-assistant-composer" onSubmit={submit}><input value={text} onChange={e => { interactionTurnRef.current += 1; stopSpeaking(); cancelVoiceInput(); speakNextResponseRef.current = false; setText(e.target.value); }} maxLength={1000} placeholder={recording ? 'Listening…' : transcribing ? 'Recognizing speech…' : pendingField ? 'Reply for ' + pendingField.replaceAll('_', ' ') + '…' : 'Ask or give a one-line answer…'}/><button type="button" className={recording ? 'voice-recording' : ''} disabled={!speechAvailable || busy} onClick={startVoiceInput} aria-label="Speak to assistant" title={transcribing ? 'Start a new recording to cancel transcription' : speechAvailable ? 'Speak to assistant' : 'Voice input unavailable in this browser'}><Mic size={16}/></button><button type="button" onClick={() => speak([...messages].reverse().find(m => m.role === 'assistant')?.text)} aria-label="Read last answer aloud" title="Read last answer aloud"><Volume2 size={16}/></button><button type="submit" disabled={busy || !text.trim()} aria-label="Send"><Send size={16}/></button></form>
    </>
  </aside>;
}

function FlightCard({ flight, selected, onSelect, fareCode, onFare }) {
  const fares = (flight.fare_options || []).filter(f => f.cabin === flight.cabin_requested);
  return <article className={`portal-flight-card ${selected ? 'selected' : ''}`}>
    <div className="portal-flight-topline"><span className="portal-carrier-mark">E</span><div className="portal-flight-number"><strong>Emirates-inspired flight</strong><small>{flight.flight_number} · Illustrative schedule</small></div><span className="portal-stop-badge">{flight.stop_label}</span></div>
    <div className="portal-flight-timeline"><div><strong>{flight.departure_time}</strong><span>{flight.origin.iata}</span><small>{niceDate(flight.departure_date, { day: 'numeric', month: 'short' })}</small></div><div className="portal-flight-duration"><span>{flight.duration_label}</span><div><i/><span/><i/></div><small>{flight.stops ? `${flight.stops} stop` : 'Non-stop'}</small></div><div className="align-right"><strong>{flight.arrival_time}</strong><span>{flight.destination.iata}</span><small>{niceDate(flight.arrival_date, { day: 'numeric', month: 'short' })}</small></div></div>
    <div className="portal-flight-route-copy"><span>{flight.origin.city} → {flight.destination.city}</span><small>{flight.operated_by} · all times local to demo</small></div>
    <div className="portal-fare-grid">{fares.map(fare => <button key={fare.code} type="button" className={`portal-fare-option ${fareCode === fare.code ? 'chosen' : ''}`} onClick={() => onFare(fare.code)}><span className="portal-fare-radio"/><span className="portal-fare-label">{fare.label}</span><strong>{money(fare.price)}</strong><small>{(fare.perks || farePerks[fare.code] || []).slice(0, 2).join(' · ')}</small>{fareCode === fare.code && <span className="portal-fare-picked"><Check size={12}/> Selected</span>}</button>)}</div>
    <div className="portal-flight-select-row"><span>Per passenger · one flight leg · demo fare</span><button type="button" className={`portal-btn ${selected ? 'portal-btn-outline' : 'portal-btn-primary'}`} onClick={onSelect}>{selected ? 'Selected' : 'Choose flight'} <ArrowRight size={15}/></button></div>
  </article>;
}

export default function BookingPortal({ section = 'book', onNavigate = () => {}, onBookingCreated = () => {}, lastBooking = null }) {
  const [flowStep, setFlowStep] = useState('search');
  const [searchForm, setSearchForm] = useState(() => ({
    origin: null, destination: null, originText: 'Delhi, India', destinationText: 'Dubai, United Arab Emirates',
    trip_type: 'round_trip', departure_date: localIsoDate(21), return_date: localIsoDate(28), adults: 1, children: 0, infants: 0, senior_citizens: 0, cabin: 'Economy',
  }));
  const [searchResult, setSearchResult] = useState(null);
  const [selectedOutbound, setSelectedOutbound] = useState(null);
  const [selectedOutboundFare, setSelectedOutboundFare] = useState('');
  const [selectedReturn, setSelectedReturn] = useState(null);
  const [selectedReturnFare, setSelectedReturnFare] = useState('');
  const [passengers, setPassengers] = useState([]);
  const [contact, setContact] = useState({ email: '', phone: '' });
  const [acceptedTerms, setAcceptedTerms] = useState(false);
  const [selectedExtrasByPassenger, setSelectedExtrasByPassenger] = useState({});
  const [activeExtrasPassenger, setActiveExtrasPassenger] = useState(0);
  const selectedExtras = selectedExtrasByPassenger[activeExtrasPassenger] || [];
  const setSelectedExtras = update => setSelectedExtrasByPassenger(old => { const current = old[activeExtrasPassenger] || []; const next = typeof update === 'function' ? update(current) : update; return { ...old, [activeExtrasPassenger]: next }; });
  const [skippedBookingSeats, setSkippedBookingSeats] = useState({});
  const [selectedBookingSeats, setSelectedBookingSeats] = useState({});
  const [activeSeatLeg, setActiveSeatLeg] = useState('outbound');
  const [activeSeatPassenger, setActiveSeatPassenger] = useState(0);
  const [paymentMethod, setPaymentMethod] = useState('upi');
  const [activeMenu, setActiveMenu] = useState(null);
  const [paymentSimulation, setPaymentSimulation] = useState('approve');
  const [confirmation, setConfirmation] = useState(lastBooking);
  const [manage, setManage] = useState({ pnr: lastBooking?.booking_reference || '', surname: lastBooking?.surname || '' });
  const [manageResult, setManageResult] = useState(null);
  const [flightStatus, setFlightStatus] = useState({ number: 'EK203', date: localIsoDate(21) });
  const [flightStatusResult, setFlightStatusResult] = useState(null);
  const [faqQuery, setFaqQuery] = useState('');
  const [faqResult, setFaqResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  useEffect(() => { if (lastBooking) setManage({ pnr: lastBooking.booking_reference, surname: lastBooking.surname }); }, [lastBooking]);
  const passengerCount = Math.max(1, Number(searchForm.adults) + Number(searchForm.senior_citizens || 0) + Number(searchForm.children) + Number(searchForm.infants));
  const chosenOutbound = useMemo(() => searchResult?.outbound_flights?.find(f => f.flight_id === selectedOutbound), [searchResult, selectedOutbound]);
  const chosenReturn = useMemo(() => searchResult?.return_flights?.find(f => f.flight_id === selectedReturn), [searchResult, selectedReturn]);
  const outboundFare = chosenOutbound?.fare_options?.find(f => f.code === selectedOutboundFare);
  const returnFare = chosenReturn?.fare_options?.find(f => f.code === selectedReturnFare);
  const fareSubtotal = ((outboundFare?.price || 0) + (returnFare?.price || 0)) * passengerCount;
  const extraSubtotal = Object.values(selectedExtrasByPassenger).flat().reduce((sum, code) => sum + (extraCatalog.find(e => e.code === code)?.price || 0), 0);
  const totalBeforeConfirm = fareSubtotal + extraSubtotal;
  const isRoundTrip = searchResult?.search?.trip_type === 'round_trip';

  useEffect(() => {
    api('/api/v1/health').then(data => setNotice(`Demo environment connected · ${data.mode} · no live airline systems`)).catch(() => setNotice('Backend not reachable. Start FastAPI on port 8000 to use this site.'));
    api('/api/v1/airports/popular').then(data => {
      const list = data.airports || [];
      setSearchForm(old => ({ ...old, origin: old.origin || list.find(a => a.iata === 'DEL') || null, destination: old.destination || list.find(a => a.iata === 'DXB') || null }));
    }).catch(() => {});
  }, []);
  useEffect(() => {
    if (section === 'book') setFlowStep('search');
    if (section === 'manage') { setManageResult(null); setError(''); }
    if (section === 'status') { setFlightStatusResult(null); setError(''); }
    if (section === 'help') { setFaqResult(null); setError(''); }
  }, [section]);

  const act = async (fn) => { setBusy(true); setError(''); setNotice(''); try { await fn(); } catch (e) { setError(e.message || 'Something went wrong. Try again.'); } finally { setBusy(false); } };

  const searchFlights = async (e) => {
    e?.preventDefault();
    await act(async () => {
      if (!searchForm.origin?.iata || !searchForm.destination?.iata) throw new Error('Choose both origin and destination from the airport suggestions.');
      const body = {
        origin: searchForm.origin.iata, destination: searchForm.destination.iata, trip_type: searchForm.trip_type,
        departure_date: searchForm.departure_date, return_date: searchForm.trip_type === 'round_trip' ? searchForm.return_date : null,
        adults: Number(searchForm.adults) + Number(searchForm.senior_citizens || 0), children: Number(searchForm.children), infants: Number(searchForm.infants), cabin: searchForm.cabin,
      };
      const data = await api('/api/v1/flights/search', { method: 'POST', body: JSON.stringify(body) });
      setSearchResult(data); setSelectedOutbound(null); setSelectedOutboundFare(''); setSelectedReturn(null); setSelectedReturnFare(''); setPassengers([]); setSelectedExtrasByPassenger({}); setActiveExtrasPassenger(0); setSelectedBookingSeats({}); setSkippedBookingSeats({}); setActiveSeatLeg('outbound'); setActiveSeatPassenger(0); setConfirmation(null); setFlowStep('outbound');
    });
  };

  const createPassengers = () => {
    const group = [];
    for (let i = 0; i < Number(searchForm.adults); i++) group.push({ title: 'MR', passenger_type: 'ADT', senior_citizen: false, first_name: '', middle_name: '', last_name: '', date_of_birth: '', nationality: '', gender: '', accompanying_adult_index: null });
    for (let i = 0; i < Number(searchForm.senior_citizens || 0); i++) group.push({ title: 'MR', passenger_type: 'ADT', senior_citizen: true, first_name: '', middle_name: '', last_name: '', date_of_birth: '', nationality: '', gender: '', accompanying_adult_index: null });
    for (let i = 0; i < Number(searchForm.children); i++) group.push({ title: 'MSTR', passenger_type: 'CHD', senior_citizen: false, first_name: '', middle_name: '', last_name: '', date_of_birth: '', nationality: '', gender: '', accompanying_adult_index: null });
    for (let i = 0; i < Number(searchForm.infants); i++) group.push({ title: 'MSTR', passenger_type: 'INF', senior_citizen: false, first_name: '', middle_name: '', last_name: '', date_of_birth: '', nationality: '', gender: '', accompanying_adult_index: null });
    setPassengers(group); setFlowStep('passengers');
  };
  const confirmBooking = async (e) => {
    e?.preventDefault();
    await act(async () => {
      const body = {
        search_id: searchResult.search_id,
        outbound_flight_id: selectedOutbound,
        outbound_fare_family: selectedOutboundFare,
        return_flight_id: isRoundTrip ? selectedReturn : null,
        return_fare_family: isRoundTrip ? selectedReturnFare : null,
        passengers: passengers.map(p => ({ ...p, date_of_birth: p.date_of_birth || null })),
        contact_email: contact.email, contact_phone: contact.phone, accepted_terms: acceptedTerms,
        extras: [...new Set(Object.values(selectedExtrasByPassenger).flat())], passenger_extras: Object.fromEntries(passengers.map((_, idx) => [String(idx), selectedExtrasByPassenger[idx] || []])), selected_seats: selectedBookingSeats, payment_simulation: paymentSimulation,
      };
      const result = await api('/api/v1/bookings/confirm', { method: 'POST', body: JSON.stringify(body) });
      setConfirmation(result); onBookingCreated(result); setFlowStep('confirmation'); setNotice('Demo booking created. No real payment was taken.');
    });
  };
  const lookupBooking = async (e) => {
    e?.preventDefault();
    await act(async () => {
      const result = await api('/api/v1/bookings/lookup', { method: 'POST', body: JSON.stringify({ record_locator: manage.pnr.trim(), last_name: manage.surname.trim() }) });
      setManageResult(result.session); setNotice('Synthetic booking loaded from the demo booking service.');
    });
  };
  const checkFlightStatus = async (e) => {
    e?.preventDefault(); await act(async () => {
      const qs = new URLSearchParams({ flight_number: flightStatus.number.trim().toUpperCase(), departure_date: flightStatus.date });
      setFlightStatusResult(await api(`/api/v1/flight-status?${qs.toString()}`));
    });
  };
  const submitFaq = async (e) => {
    e?.preventDefault(); await act(async () => setFaqResult(await api('/api/v1/site-assistant/chat', { method: 'POST', body: JSON.stringify({ message: faqQuery, page: 'help' }) })));
  };

  const navItems = [
    { key: 'book', label: 'Book', items: [{ label: 'Book a flight', target: 'book' }, { label: 'Flight status', target: 'status' }] },
    { key: 'manage', label: 'Manage', items: [{ label: 'Manage booking', target: 'manage' }, { label: 'Online check-in', target: 'checkin' }, { label: 'Flight status', target: 'status' }] },
    { key: 'experience', label: 'Experience', items: [{ label: 'Onboard dining', notice: 'Experience pages are visual placeholders in this synthetic POC. Booking and check-in remain functional.' }, { label: 'Seats and extras', target: 'bookSeats' }] },
    { key: 'destinations', label: 'Where we fly', items: [{ label: 'Dubai · DXB', destination: ['Dubai', 'United Arab Emirates'] }, { label: 'London · LHR', destination: ['London', 'United Kingdom'] }, { label: 'Malé · MLE', destination: ['Malé', 'Maldives'] }] },
    { key: 'skywards', label: 'Skywards', items: [{ label: 'Emirates Skywards', notice: 'Loyalty sign-in is not connected in this demo. No account or loyalty data is collected.' }] },
    { key: 'help', label: 'Help', target: 'help' },
  ];
  const handleNav = key => { setMobileNavOpen(false); if (key === 'checkin') { onNavigate('checkin', { pnr: manageResult?.booking?.record_locator || lastBooking?.booking_reference || manage.pnr, surname: manageResult?.booking?.last_name || lastBooking?.surname || manage.surname }); return; } if (key === 'book') { setFlowStep('search'); setError(''); setNotice(''); } onNavigate(key); };
  const activateNavItem = item => {
    setActiveMenu(null); setMobileNavOpen(false);
    if (item.destination) { const [city,country] = item.destination; setSearchForm(old => ({ ...old, destination: null, destinationText: `${city}, ${country}` })); onNavigate('book'); return; }
    if (item.notice) { setNotice(item.notice); return; }
    if (item.target === 'bookSeats') { setNotice('Choose a flight and passenger details first; seat selection appears after extras.'); return; }
    handleNav(item.target || 'book');
  };
  const setPassengerField = (index, field, value) => setPassengers(old => old.map((p, i) => i === index ? { ...p, [field]: value } : p));

  const handleAssistantAction = async (action) => {
    if (action.type === 'UPDATE_PASSENGER_FIELD') {
      setPassengerField(Number(action.passenger_index || 0), action.field, action.value);
    } else if (action.type === 'UPDATE_CONTACT_FIELD') {
      setContact(old => ({ ...old, [action.field]: action.value }));
    } else if (action.type === 'UPDATE_ACCEPTED_TERMS') {
      setAcceptedTerms(Boolean(action.value));
    } else if (action.type === 'NAVIGATE_FLOW_STEP') {
      setError('');
      setFlowStep(action.target);
    } else if (action.type === 'BACK_MAIN_FLOW') {
      const page = action.page;
      const previous = { outbound: 'search', return: 'outbound', passengers: isRoundTrip ? 'return' : 'outbound', extras: 'passengers', seats: 'extras', payment: 'seats', confirmation: 'payment' }[page];
      if (previous) setFlowStep(previous);
      else if (page === 'search') handleNav('book');
    } else if (action.type === 'ADVANCE_MAIN_FLOW') {
      const page = action.page;
      if (page === 'search') { await searchFlights(); }
      else if (page === 'outbound') {
        if (!selectedOutbound || !selectedOutboundFare) { setError('Select a departure flight and fare before moving to the next page.'); return; }
        if (isRoundTrip) setFlowStep('return'); else createPassengers();
      } else if (page === 'return') {
        if (!selectedReturn || !selectedReturnFare) { setError('Select a return flight and fare before moving to passenger details.'); return; }
        createPassengers();
      } else if (page === 'passengers') {
        document.querySelector('.portal-flow-panel form')?.requestSubmit();
      } else if (page === 'extras') {
        if (activeExtrasPassenger + 1 < passengers.length) setActiveExtrasPassenger(index => index + 1); else setFlowStep('seats');
      } else if (page === 'seats') { advanceBookingSeatFocus(false); }
      else if (page === 'payment') { await confirmBooking(); }
    } else if (action.type === 'RUN_FLIGHT_SEARCH') {
      await searchFlights();
    } else if (action.type === 'SELECT_FLIGHT') {
      if (action.leg === 'return') {
        setSelectedReturn(action.flight_id);
        setSelectedReturnFare(action.fare_code);
        createPassengers();
      } else {
        setSelectedOutbound(action.flight_id);
        setSelectedOutboundFare(action.fare_code);
        if (searchResult?.search?.trip_type === 'round_trip') setFlowStep('return');
        else createPassengers();
      }
    } else if (action.type === 'TOGGLE_EXTRAS') {
      const idx = Math.min(Math.max(0, Number(action.passenger_index ?? activeExtrasPassenger)), Math.max(0, passengers.length - 1));
      setSelectedExtrasByPassenger(old => ({ ...old, [idx]: [...new Set([...(old[idx] || []), ...(action.codes || [])])] }));
      setActiveExtrasPassenger(idx);
    } else if (action.type === 'REMOVE_EXTRAS') {
      const idx = Math.min(Math.max(0, Number(action.passenger_index ?? activeExtrasPassenger)), Math.max(0, passengers.length - 1));
      const remove = new Set(action.codes || []); setSelectedExtrasByPassenger(old => ({ ...old, [idx]: (old[idx] || []).filter(code => !remove.has(code)) })); setActiveExtrasPassenger(idx);
    } else if (action.type === 'ADVANCE_EXTRAS_PASSENGER' || action.type === 'SKIP_EXTRAS_PASSENGER') {
      const idx = Math.min(Math.max(0, Number(action.passenger_index ?? activeExtrasPassenger)), Math.max(0, passengers.length - 1));
      const current = action.type === 'SKIP_EXTRAS_PASSENGER' ? [] : (selectedExtrasByPassenger[idx] || []);
      setSelectedExtrasByPassenger(old => ({ ...old, [idx]: current }));
      if (idx + 1 < passengers.length) setActiveExtrasPassenger(idx + 1); else setFlowStep('seats');
    } else if (action.type === 'SKIP_BOOKING_SEAT') {
      const targetLeg = action.leg === 'return' && isRoundTrip ? 'return' : 'outbound';
      const targetPassenger = Math.min(Math.max(0, Number(action.passenger_index || 0)), Math.max(0, passengers.length - 1));
      setActiveSeatLeg(targetLeg); setActiveSeatPassenger(targetPassenger); setFlowStep('seats');
      advanceBookingSeatFocus(true, targetLeg, targetPassenger);
    } else if (action.type === 'NEXT_BOOKING_SEAT') {
      const targetLeg = action.leg === 'return' && isRoundTrip ? 'return' : 'outbound';
      const targetPassenger = Math.min(Math.max(0, Number(action.passenger_index || 0)), Math.max(0, passengers.length - 1));
      setActiveSeatLeg(targetLeg); setActiveSeatPassenger(targetPassenger); setFlowStep('seats');
      advanceBookingSeatFocus(false, targetLeg, targetPassenger);
    } else if (action.type === 'CLEAR_BOOKING_SEATS') {
      setSelectedBookingSeats({}); setSkippedBookingSeats(Object.fromEntries(requiredBookingSeatKeys.map(key => [key, true])));
    } else if (action.type === 'SELECT_BOOKING_SEAT') {
      const leg = action.leg === 'return' && isRoundTrip ? 'return' : 'outbound';
      const idx = Math.min(Math.max(0, Number(action.passenger_index || 0)), Math.max(0, passengers.length - 1));
      const seat = String(action.seat_code || '').toUpperCase();
      if (!/^(?:[1-9]|[1-3][0-9]|40)[A-F]$/.test(seat)) throw new Error('Choose a seat in the displayed format, such as 1A.');
      const unavailable = ['1B','2D','3A','5F','7C','9E','10B'];
      const taken = Object.entries(selectedBookingSeats).some(([key,value]) => key.startsWith(leg + ':') && key !== `${leg}:${idx}` && value === seat);
      if (unavailable.includes(seat) || taken) throw new Error(`Seat ${seat} is not available in the mock seat map. Choose another available seat.`);
      setSelectedBookingSeats(old => ({ ...old, [`${leg}:${idx}`]: seat })); setSkippedBookingSeats(old => ({ ...old, [`${leg}:${idx}`]: false })); setActiveSeatLeg(leg); setActiveSeatPassenger(idx); setFlowStep('seats');
    } else if (action.type === 'SET_PAYMENT_METHOD') {
      const method = String(action.value || 'upi').toLowerCase();
      if (!['upi','card','netbanking','wallet'].includes(method)) throw new Error('Choose UPI, card, net banking, or wallet from the visible payment options.');
      setPaymentMethod(method);
    } else if (action.type === 'LOOKUP_MANAGE_BOOKING') {
      const pnr = String(action.record_locator || '').trim().toUpperCase();
      const surname = String(action.last_name || '').trim().toUpperCase();
      await act(async () => { const result = await api('/api/v1/bookings/lookup', { method: 'POST', body: JSON.stringify({ record_locator: pnr, last_name: surname }) }); setManage({ pnr, surname }); setManageResult(result.session); setNotice('Synthetic booking loaded using the same lookup service as the page form.'); });
    } else if (action.type === 'SET_PAYMENT_SIMULATION') {
      setPaymentSimulation(action.value === 'decline' ? 'decline' : 'approve');
    } else if (action.type === 'CONFIRM_DEMO_BOOKING') {
      await confirmBooking();
    }
  };

  const renderStepBar = () => {
    const order = ['outbound', 'passengers', 'extras', 'seats', 'payment', 'confirmation'];
    const activeIdx = flowStep === 'return' ? 0 : order.indexOf(flowStep);
    return <div className="portal-progress">{[['outbound', 'Flights'], ['passengers', 'Passengers'], ['extras', 'Extras'], ['seats', 'Seats'], ['payment', 'Payment'], ['confirmation', 'Confirmation']].map(([id, title], idx) => <div key={id} className={`portal-progress-step ${activeIdx === idx ? 'active' : activeIdx > idx ? 'complete' : ''}`}><span>{activeIdx > idx ? <Check size={13}/> : idx + 1}</span><small>{title}</small></div>)}</div>;
  };

  const renderSearchForm = () => <>
    <div className="portal-hero"><div className="portal-hero-copy"><span className="portal-eyebrow"><Sparkles size={14}/> EXPERIENCE EMIRATES-INSPIRED TRAVEL</span><h1>Discover the world.<br/><em>Fly with care.</em></h1><p>Explore destinations, compare illustrative fares and prepare your journey in one guided experience.</p><div className="portal-hero-perks"><span><ShieldCheck size={15}/> Flexible options</span><span><Compass size={15}/> Global destinations</span><span><Ticket size={15}/> Simple booking</span></div></div><div className="portal-hero-art"><div className="portal-orbit orbit-one"/><div className="portal-orbit orbit-two"/><div className="portal-skyline"><i/><i/><i/><i/><i/><i/><i/><i/><i/></div><div className="portal-plane-illustration"><Plane size={90} strokeWidth={0.8}/></div><div className="portal-hero-route"><span>DXB</span><i/><span>THE WORLD</span></div></div></div>
    <div className="portal-service-tabs" role="navigation" aria-label="Travel services"><button type="button" className="active" onClick={() => handleNav('book')}><Plane size={16}/> Book a flight</button><button type="button" onClick={() => handleNav('manage')}><Ticket size={16}/> Manage booking</button><button type="button" onClick={() => handleNav('checkin')}><CheckCircle2 size={16}/> Check in</button><button type="button" onClick={() => handleNav('status')}><Clock3 size={16}/> Flight status</button></div>
    <section className="portal-search-card"><div className="portal-card-heading"><div><span className="portal-eyebrow">START YOUR JOURNEY</span><h2>Where would you like to go?</h2></div><span className="portal-mock-pill"><i/> DEMO FLIGHTS</span></div>
      <form onSubmit={searchFlights}>
        <div className="portal-trip-row"><label className={`portal-radio-option ${searchForm.trip_type === 'round_trip' ? 'selected' : ''}`}><input type="radio" name="trip" value="round_trip" checked={searchForm.trip_type === 'round_trip'} onChange={() => setSearchForm(s => ({ ...s, trip_type: 'round_trip', return_date: s.return_date || localIsoDate(28) }))}/> Round trip</label><label className={`portal-radio-option ${searchForm.trip_type === 'one_way' ? 'selected' : ''}`}><input type="radio" name="trip" value="one_way" checked={searchForm.trip_type === 'one_way'} onChange={() => setSearchForm(s => ({ ...s, trip_type: 'one_way', return_date: '' }))}/> One way</label><span className="portal-form-note">All displayed prices are illustrative</span></div>
        <div className="portal-route-grid"><AirportField label="From" value={searchForm.originText} selected={searchForm.origin} onChange={v => setSearchForm(s => ({ ...s, originText: v }))} onSelect={v => setSearchForm(s => ({ ...s, origin: v }))} placeholder="City or airport"/><button type="button" className="portal-swap-route" title="Swap airports" onClick={() => setSearchForm(s => ({ ...s, origin: s.destination, destination: s.origin, originText: s.destinationText, destinationText: s.originText }))}><ArrowLeft size={15}/><ArrowRight size={15}/></button><AirportField label="To" value={searchForm.destinationText} selected={searchForm.destination} onChange={v => setSearchForm(s => ({ ...s, destinationText: v }))} onSelect={v => setSearchForm(s => ({ ...s, destination: v }))} placeholder="Where to?"/></div>
        <div className="portal-search-fields"><div className="portal-field"><label><CalendarDays size={14}/> Departure</label><input required type="date" min={localIsoDate()} value={searchForm.departure_date} onChange={e => setSearchForm(s => ({ ...s, departure_date: e.target.value, return_date: s.return_date && s.return_date < e.target.value ? e.target.value : s.return_date }))}/></div>{searchForm.trip_type === 'round_trip' && <div className="portal-field"><label><CalendarDays size={14}/> Return</label><input required type="date" min={searchForm.departure_date || localIsoDate()} value={searchForm.return_date} onChange={e => setSearchForm(s => ({ ...s, return_date: e.target.value }))}/></div>}<div className="portal-field"><label><UserRound size={14}/> Adults</label><select value={searchForm.adults} onChange={e => setSearchForm(s => ({ ...s, adults: Number(e.target.value), infants: Math.min(Number(s.infants), Number(e.target.value)) }))}>{[1,2,3,4,5,6,7,8,9].map(x => <option key={x} value={x}>{x} {x === 1 ? 'adult' : 'adults'}</option>)}</select></div><div className="portal-field"><label><UserRound size={14}/> Children</label><select value={searchForm.children} onChange={e => setSearchForm(s => ({ ...s, children: Number(e.target.value) }))}>{[0,1,2,3,4,5,6,7,8].map(x => <option key={x} value={x}>{x} {x === 1 ? 'child' : 'children'}</option>)}</select></div><div className="portal-field"><label><UserRound size={14}/> Infants</label><select value={searchForm.infants} onChange={e => setSearchForm(s => ({ ...s, infants: Number(e.target.value) }))}>{[0,1,2,3,4,5,6,7,8].filter(x => x <= Number(searchForm.adults) + Number(searchForm.senior_citizens || 0)).map(x => <option key={x} value={x}>{x} {x === 1 ? 'infant' : 'infants'}</option>)}</select></div><div className="portal-field"><label><UserRound size={14}/> Senior citizens</label><select value={searchForm.senior_citizens || 0} onChange={e => setSearchForm(s => ({ ...s, senior_citizens: Number(e.target.value), infants: Math.min(Number(s.infants), Number(s.adults) + Number(e.target.value)) }))}>{[0,1,2,3,4,5,6,7,8].map(x => <option key={x} value={x}>{x} {x === 1 ? 'senior citizen' : 'senior citizens'}</option>)}</select></div><div className="portal-field"><label><Compass size={14}/> Cabin</label><select value={searchForm.cabin} onChange={e => setSearchForm(s => ({ ...s, cabin: e.target.value }))}>{['Economy','Premium Economy','Business','First'].map(x => <option key={x}>{x}</option>)}</select></div></div>
        <div className="portal-search-submit"><span><LockKeyholeInline/> No real ticket or payment is created</span><button className="portal-btn portal-btn-primary portal-btn-large" type="submit" disabled={busy}>{busy ? 'Searching…' : 'Search flights'} <Search size={17}/></button></div>
      </form>
    </section>
    <section className="portal-destination-strip"><div><span className="portal-eyebrow">FIND YOUR INSPIRATION</span><h2>Travel is waiting</h2><p>Explore destinations from a local airport catalogue.</p></div><div className="portal-destination-cards">{[{code:'DXB',city:'Dubai',country:'United Arab Emirates',text:'A city of remarkable skylines'},{code:'LHR',city:'London',country:'United Kingdom',text:'Culture around every corner'},{code:'MLE',city:'Malé',country:'Maldives',text:'Island escapes and turquoise seas'}].map((d,i)=><button key={d.code} className={`portal-destination-card dest-${i}`} onClick={() => setSearchForm(s => ({ ...s, destination: null, destinationText: `${d.city}, ${d.country}` }))}><span>{d.code}</span><strong>{d.city}</strong><small>{d.text}</small><span className="portal-destination-cta">Explore <ArrowRight size={14}/></span></button>)}</div></section>
  </>;

  const renderFlights = (direction) => {
    const flights = direction === 'outbound' ? searchResult?.outbound_flights || [] : searchResult?.return_flights || [];
    const currentFlight = direction === 'outbound' ? selectedOutbound : selectedReturn;
    const currentFare = direction === 'outbound' ? selectedOutboundFare : selectedReturnFare;
    const setCurrentFlight = direction === 'outbound' ? setSelectedOutbound : setSelectedReturn;
    const setCurrentFare = direction === 'outbound' ? setSelectedOutboundFare : setSelectedReturnFare;
    const from = direction === 'outbound' ? searchResult?.origin : searchResult?.destination;
    const to = direction === 'outbound' ? searchResult?.destination : searchResult?.origin;
    return <section className="portal-flow-panel"><div className="portal-flow-heading"><button className="portal-back-link" onClick={() => setFlowStep('search')}><ArrowLeft size={15}/> Edit search</button><span className="portal-eyebrow">STEP 1 · CHOOSE A FLIGHT</span><h1>{direction === 'outbound' ? 'Choose your departure' : 'Choose your return'}</h1><p>{airportLabel(from)} <ArrowRight size={13}/> {airportLabel(to)} · {niceDate(direction === 'outbound' ? searchResult.search.departure_date : searchResult.search.return_date)}</p></div><div className="portal-search-summary"><span><CalendarDays size={15}/> {niceDate(searchResult.search.departure_date)}{isRoundTrip ? ` – ${niceDate(searchResult.search.return_date)}` : ''}</span><span><UserRound size={15}/> {searchResult.passenger_count} passenger{searchResult.passenger_count === 1 ? '' : 's'}</span><span><BriefcaseBusiness size={15}/> {searchResult.search.cabin}</span><button onClick={() => setFlowStep('search')}>Change search</button></div>{flights.map(f => <FlightCard key={f.flight_id} flight={f} selected={currentFlight === f.flight_id} fareCode={currentFlight === f.flight_id ? currentFare : ''} onFare={code => { setCurrentFlight(f.flight_id); setCurrentFare(code); }} onSelect={() => { setCurrentFlight(f.flight_id); if (!currentFare || currentFlight !== f.flight_id) { const firstFare = f.fare_options.find(x => x.cabin === f.cabin_requested); setCurrentFare(firstFare?.code || ''); } }} />)}<div className="portal-sticky-next"><div><small>Selected fare</small><strong>{money((direction === 'outbound' ? outboundFare?.price : returnFare?.price) || 0)} <span>/ passenger</span></strong></div><button className="portal-btn portal-btn-primary" disabled={!currentFlight || !currentFare} onClick={() => { if (direction === 'outbound' && isRoundTrip) setFlowStep('return'); else createPassengers(); }}>{direction === 'outbound' && isRoundTrip ? 'Choose return' : 'Continue to passengers'} <ArrowRight size={16}/></button></div></section>;
  };

  const renderPassengers = () => {
    const adultCompanionOptions = passengers.map((passenger, passengerIndex) => ({ passenger, passengerIndex })).filter(({ passenger }) => passenger.passenger_type === 'ADT' && ageOnDate(passenger.date_of_birth, searchForm.departure_date) >= 16);
    return <section className="portal-flow-panel"><div className="portal-flow-heading"><button className="portal-back-link" onClick={() => setFlowStep(isRoundTrip ? 'return' : 'outbound')}><ArrowLeft size={15}/> Back to flights</button><span className="portal-eyebrow">STEP 2 · PASSENGER DETAILS</span><h1>Who is travelling?</h1><p>Enter passenger names exactly as they appear on the travel document. Date of birth is not inferred by the assistant.</p></div><form onSubmit={e => { e.preventDefault(); if (passengers.some(p => !p.first_name.trim() || !p.last_name.trim())) { setError('Enter a first name and surname for every passenger.'); return; } const ageIssues = passengers.map((p, i) => { if (!p.date_of_birth) return `Passenger ${i + 1}: date of birth is required to validate age, passenger category and accompaniment rules.`; const dob = new Date(`${p.date_of_birth}T12:00:00`); const dep = new Date(`${searchForm.departure_date}T12:00:00`); const age = dep.getFullYear() - dob.getFullYear() - ((dep.getMonth() < dob.getMonth() || (dep.getMonth() === dob.getMonth() && dep.getDate() < dob.getDate())) ? 1 : 0); const ageToday = ageOnDate(p.date_of_birth, localIsoDate()); const expected = age < 2 ? 'INF' : age < 12 ? 'CHD' : 'ADT'; return ageToday === null || ageToday < 0 ? `Passenger ${i + 1}: date of birth must be a valid past date; future birth dates are not accepted.` : p.senior_citizen && age < 60 ? `Passenger ${i + 1}: senior citizen requires age 60 or above on departure; this birth date gives age ${age}.` : age < 16 && p.accompanying_adult_index == null ? `Passenger ${i + 1}: traveller is under 16 and needs an accompanying adult selected or the strict unaccompanied-minor rule applies.` : (p.accompanying_adult_index != null && (Number(p.accompanying_adult_index) === i || !adultCompanionOptions.some(({ passengerIndex }) => passengerIndex === Number(p.accompanying_adult_index)))) ? `Passenger ${i + 1}: accompanying passenger must be an adult passenger in this booking.` : age < 0 ? `Passenger ${i + 1}: date of birth is after departure.` : (age === 0 && Math.floor((dep.getTime() - dob.getTime()) / 86400000) < 7) ? `Passenger ${i + 1}: infant under 7 days needs exceptional handling and cannot use this demo.` : expected !== p.passenger_type ? `Passenger ${i + 1}: age ${age} on departure requires the ${expected === 'INF' ? 'infant (under 2)' : expected === 'CHD' ? 'child (2–11)' : 'adult (12+)'} category.` : null; }).filter(Boolean); if (ageIssues.length) { setError(ageIssues.join(' ')); return; } if (!acceptedTerms) { setError('Review and accept the demonstration booking terms before continuing.'); return; } setError(''); setFlowStep('extras'); }}><div className="portal-passenger-list">{passengers.map((p, i) => <div className="portal-passenger-card" key={i}><div className="portal-passenger-card-title"><span className="portal-passenger-number">{String(i+1).padStart(2,'0')}</span><div><strong>Passenger {i+1}</strong><small>{p.senior_citizen ? 'Senior citizen' : p.passenger_type === 'ADT' ? 'Adult' : p.passenger_type === 'CHD' ? 'Child' : 'Infant'}</small></div></div><div className="portal-passenger-fields"><div className="portal-field"><label>Title</label><select value={p.title} onChange={e => setPassengerField(i,'title',e.target.value)}>{['MR','MRS','MS','MISS','MSTR','DR'].map(x=><option key={x}>{x}</option>)}</select></div><div className="portal-field"><label>First name</label><input required maxLength={60} pattern="[A-Za-z][A-Za-z .'\-]{0,59}" title="Use letters, spaces, apostrophes, periods or hyphens; begin with a letter." value={p.first_name} onChange={e => setPassengerField(i,'first_name',e.target.value)} placeholder="First name"/></div><div className="portal-field"><label>Middle name <em>Optional</em></label><input maxLength={60} pattern="[A-Za-z .'\-]{0,60}" title="Use letters, spaces, apostrophes, periods or hyphens." value={p.middle_name} onChange={e => setPassengerField(i,'middle_name',e.target.value)} placeholder="Middle name"/></div><div className="portal-field"><label>Last name / surname</label><input required maxLength={60} pattern="[A-Za-z][A-Za-z .'\-]{0,59}" title="Use letters, spaces, apostrophes, periods or hyphens; begin with a letter." value={p.last_name} onChange={e => setPassengerField(i,'last_name',e.target.value)} placeholder="Surname"/></div><div className="portal-field"><label>Date of birth <em>Required to validate age and accompaniment rules</em></label><input type="date" required min={yearOffsetIso(searchForm.departure_date || localIsoDate(), -120)} max={p.senior_citizen ? (yearOffsetIso(searchForm.departure_date || localIsoDate(), -60) < localIsoDate() ? yearOffsetIso(searchForm.departure_date || localIsoDate(), -60) : localIsoDate(-1)) : localIsoDate(-1)} value={p.date_of_birth} onChange={e => setPassengerField(i,'date_of_birth',e.target.value)}/></div>{(p.passenger_type !== 'ADT' || (p.date_of_birth && ageOnDate(p.date_of_birth, searchForm.departure_date) < 16)) && <div className="portal-field portal-companion-field"><label>Accompanying adult {p.passenger_type === 'INF' ? <em>Required for infants</em> : <em>Under-16 accompaniment</em>}</label><select required={p.passenger_type === 'INF'} value={p.accompanying_adult_index == null ? '' : String(p.accompanying_adult_index)} onChange={e => setPassengerField(i,'accompanying_adult_index',e.target.value === '' ? null : Number(e.target.value))}><option value="">{p.passenger_type === 'INF' ? 'Select accompanying adult (required)' : 'None selected — unaccompanied-minor rules apply'}</option>{adultCompanionOptions.filter(({passengerIndex}) => passengerIndex !== i).map(({passenger,passengerIndex}) => <option key={passengerIndex} value={passengerIndex}>Passenger {passengerIndex+1} · {`${passenger.first_name || passenger.title || 'Adult'} ${passenger.last_name || ''}`.trim()}</option>)}</select><small>{p.passenger_type === 'INF' ? 'Every infant must be linked to one adult passenger. An adult cannot accompany more than one infant in this demo.' : 'Required by this demo if the traveller is under 16; a missing adult triggers the unaccompanied-minor gate.'}</small></div>}<div className="portal-field"><CountryCodeInput id={`booking-nationality-${i}`} label="Nationality / country" value={p.nationality} onChange={value => setPassengerField(i,'nationality',value)} apiBase={API} placeholder="India, IN or IND"/></div><div className="portal-field"><label>Gender (as document)</label><select required value={p.gender} onChange={e => setPassengerField(i,'gender',e.target.value)}><option value="">Select gender marker</option>{[['U','Prefer not to say'],['M','Male'],['F','Female'],['X','X / unspecified']].map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></div></div></div>)}</div><div className="portal-passenger-card"><div className="portal-passenger-card-title"><span className="portal-passenger-number"><UserRound size={18}/></span><div><strong>Contact details</strong><small>Where a demo confirmation would be sent</small></div></div><div className="portal-passenger-fields"><div className="portal-field"><label>Email address</label><input type="email" required value={contact.email} onChange={e => setContact(s=>({...s,email:e.target.value}))} placeholder="you@example.com"/></div><div className="portal-field"><label>Phone number</label><input type="tel" required minLength={7} maxLength={24} pattern="\+?[0-9]{7,18}" title="Use a country prefix and digits; optional spaces, parentheses and hyphens are allowed." value={contact.phone} onChange={e => setContact(s=>({...s,phone:e.target.value.replace(/[^0-9+]/g, '').replace(/(?!^)\+/g, '')}))} placeholder="Country code + number"/></div></div></div><label className="portal-checkbox-row"><input type="checkbox" checked={acceptedTerms} onChange={e=>setAcceptedTerms(e.target.checked)}/><span>I have reviewed these synthetic passenger details and accept the demonstration booking terms.</span></label><div className="portal-form-actions"><button type="button" className="portal-btn portal-btn-outline" onClick={() => setFlowStep(isRoundTrip ? 'return' : 'outbound')}><ArrowLeft size={15}/> Back</button><button type="submit" className="portal-btn portal-btn-primary">Continue to extras <ArrowRight size={15}/></button></div></form></section>;
  };

  const renderExtras = () => {
    const active = passengers[activeExtrasPassenger];
    const chosen = selectedExtrasByPassenger[activeExtrasPassenger] || [];
    const doneCount = Object.keys(selectedExtrasByPassenger).filter(key => Number(key) < activeExtrasPassenger).length;
    const setExtraForActive = (code, turnOn) => setSelectedExtrasByPassenger(old => ({ ...old, [activeExtrasPassenger]: turnOn ? [...new Set([...(old[activeExtrasPassenger] || []), code])] : (old[activeExtrasPassenger] || []).filter(x => x !== code) }));
    const stepPassenger = (skip = false) => {
      if (skip) setSelectedExtrasByPassenger(old => ({ ...old, [activeExtrasPassenger]: [] }));
      if (activeExtrasPassenger + 1 < passengers.length) setActiveExtrasPassenger(i => i + 1);
      else setFlowStep('seats');
    };
    return <section className="portal-flow-panel"><div className="portal-flow-heading"><button className="portal-back-link" onClick={() => setFlowStep('passengers')}><ArrowLeft size={15}/> Back to passengers</button><span className="portal-eyebrow">STEP 3 · PERSONALISE</span><h1>Add-ons for every passenger</h1><p>Select and confirm optional items for each passenger in turn. Skip one passenger without affecting the others.</p></div>
      <div className="portal-extra-passenger-tabs" role="group" aria-label="Choose passenger for extras">{passengers.map((p, idx) => <button type="button" key={idx} className={activeExtrasPassenger === idx ? 'active' : ''} onClick={() => setActiveExtrasPassenger(idx)}>Passenger {idx + 1}<small>{(selectedExtrasByPassenger[idx] || []).length} selected</small></button>)}</div>
      <div className="portal-inline-notice"><UserRound size={17}/><span>Passenger {Math.min(activeExtrasPassenger + 1, Math.max(1, passengers.length))} of {passengers.length || 0} · {active ? `${active.first_name || 'Passenger'} ${active.last_name || ''}` : 'No passenger selected'}</span></div>
      <div className="portal-extra-list">{extraCatalog.map(item=>{const Icon=item.icon;const checked=chosen.includes(item.code);return <button type="button" key={item.code} className={`portal-extra-card ${checked?'chosen':''}`} onClick={()=>setExtraForActive(item.code,!checked)}><span className="portal-extra-icon"><Icon size={23}/></span><span className="portal-extra-copy"><strong>{item.title}</strong><small>{item.detail}</small></span><span className="portal-extra-price">{item.price ? `+${money(item.price)}` : 'Included'}<small>for this passenger</small></span><span className="portal-extra-check">{checked?<Check size={15}/>:<span/>}</span></button>})}</div>
      <div className="portal-inline-notice"><Sparkles size={17}/><span>Passenger add-on subtotal: {money(chosen.reduce((sum,code)=>sum+Number(extraCatalog.find(x=>x.code===code)?.price||0),0))}. All passengers’ selected add-ons are included in the demo total.</span></div>
      <div className="portal-form-actions"><button className="portal-btn portal-btn-outline" onClick={()=>setFlowStep('passengers')}><ArrowLeft size={15}/> Back</button><button className="portal-btn portal-btn-outline" onClick={()=>stepPassenger(true)}>{activeExtrasPassenger + 1 < passengers.length ? 'Skip this passenger' : 'Skip last passenger'}</button><button className="portal-btn portal-btn-primary" onClick={()=>stepPassenger(false)}>{activeExtrasPassenger + 1 < passengers.length ? 'Confirm extras & next passenger' : 'Confirm choices & continue to seats'} <ArrowRight size={16}/></button></div>
      <div className="portal-inline-notice"><CheckCircle2 size={16}/><span>{passengers.map((p,idx)=>`${idx+1}: ${(selectedExtrasByPassenger[idx]||[]).length} add-on(s)`).join(' · ') || 'Passenger add-ons not configured yet'}</span></div>
    </section>;
  };

  const seatUnavailable = ['1B','2D','3A','5F','7C','9E','10B'];
  const bookingSeatLegs = isRoundTrip ? ['outbound', 'return'] : ['outbound'];
  const requiredBookingSeatKeys = bookingSeatLegs.flatMap(leg => passengers.map((_, index) => `${leg}:${index}`));
  const allBookingSeatsSelected = passengers.length > 0 && requiredBookingSeatKeys.every(key => Boolean(selectedBookingSeats[key]) || Boolean(skippedBookingSeats[key]));
  const advanceBookingSeatFocus = (skipCurrent = false, targetLeg = activeSeatLeg, targetPassenger = activeSeatPassenger) => {
    const currentKey = `${targetLeg}:${targetPassenger}`;
    if (!skipCurrent && !selectedBookingSeats[currentKey]) { setError('Choose a seat or skip this passenger for this journey before continuing.'); return; }
    const nextSkipped = skipCurrent ? { ...skippedBookingSeats, [currentKey]: true } : { ...skippedBookingSeats };
    const nextSelected = skipCurrent ? Object.fromEntries(Object.entries(selectedBookingSeats).filter(([key]) => key !== currentKey)) : { ...selectedBookingSeats };
    setSkippedBookingSeats(nextSkipped); setSelectedBookingSeats(nextSelected);
    const nextKey = requiredBookingSeatKeys.find(key => !nextSelected[key] && !nextSkipped[key]);
    if (nextKey) { const [leg, idx] = nextKey.split(':'); setActiveSeatLeg(leg); setActiveSeatPassenger(Number(idx)); }
    else { setFlowStep('payment'); setNotice('Every passenger and journey has a confirmed seat or an explicit skip.'); }
  };
  const skipAllBookingSeats = () => { setSelectedBookingSeats({}); setSkippedBookingSeats(Object.fromEntries(requiredBookingSeatKeys.map(key => [key, true]))); setFlowStep('payment'); };
  const renderSeats = () => <section className="portal-flow-panel portal-seats-panel">
    <div className="portal-flow-heading"><button className="portal-back-link" onClick={() => setFlowStep('extras')}><ArrowLeft size={15}/> Back to extras</button><span className="portal-eyebrow">STEP 4 · SEAT SELECTION</span><h1>Choose your seat</h1><p>Select seats for every passenger and journey, or ask the assistant to select a seat by voice or text. Illustrative inventory only.</p></div>
    {bookingSeatLegs.length > 1 && <div className="portal-seat-leg-tabs">{bookingSeatLegs.map(leg => <button type="button" key={leg} className={activeSeatLeg === leg ? 'active' : ''} onClick={() => setActiveSeatLeg(leg)}>{leg === 'outbound' ? 'Outbound' : 'Return'} journey</button>)}</div>}
    <div className="portal-seat-passengers">{passengers.map((p,index) => <button type="button" key={index} className={activeSeatPassenger === index ? 'active' : ''} onClick={() => setActiveSeatPassenger(index)}><span>{index + 1}</span><span>{p.first_name || `Passenger ${index + 1}`} {p.last_name || ''}</span><strong>{selectedBookingSeats[`${activeSeatLeg}:${index}`] || (skippedBookingSeats[`${activeSeatLeg}:${index}`] ? 'Skipped' : 'Choose seat')}</strong></button>)}</div>
    <div className="portal-seat-layout"><div className="portal-seat-legend"><span><i className="seat-legend-free"/>Available</span><span><i className="seat-legend-taken"/>Unavailable</span><span><i className="seat-legend-picked"/>Selected</span></div><div className="portal-aircraft-seatmap"><div className="portal-seat-cabin-top"><Plane size={22}/><span>FRONT OF AIRCRAFT · {activeSeatLeg.toUpperCase()}</span></div><div className="portal-seat-rows">{Array.from({length:40},(_,i)=>i+1).map(row => <div className="portal-seat-row" key={row}><span className="portal-seat-row-number">{row}</span>{['A','B','C','aisle','D','E','F'].map(letter => { if (letter === 'aisle') return <span className="portal-seat-aisle" key={letter}/>; const seat = `${row}${letter}`; const selected = selectedBookingSeats[`${activeSeatLeg}:${activeSeatPassenger}`] === seat; const used = seatUnavailable.includes(seat) || Object.entries(selectedBookingSeats).some(([key,value]) => key.startsWith(activeSeatLeg + ':') && key !== `${activeSeatLeg}:${activeSeatPassenger}` && value === seat); return <button type="button" key={letter} className={`portal-seat ${selected?'selected':used?'unavailable':'available'}`} disabled={used} title={used ? `${seat} unavailable` : `Select seat ${seat}`} onClick={() => { const key = `${activeSeatLeg}:${activeSeatPassenger}`; setSelectedBookingSeats(old => ({...old,[key]:seat})); setSkippedBookingSeats(old => ({ ...old, [key]: false })); }}>{letter}</button>; })}</div>)}</div><div className="portal-seat-cabin-bottom">CABIN · DEMO SEAT MAP</div></div></div>
    <div className="portal-seat-summary"><span>{requiredBookingSeatKeys.filter(key => selectedBookingSeats[key] || skippedBookingSeats[key]).length} of {requiredBookingSeatKeys.length} passenger/journey seat choices confirmed</span><strong>{allBookingSeatsSelected ? 'All seats selected or skipped' : 'Selection incomplete'}</strong></div>
    <div className="portal-form-actions"><button className="portal-btn portal-btn-outline" onClick={() => setFlowStep('extras')}><ArrowLeft size={15}/> Back</button><button className="portal-btn portal-btn-outline" onClick={() => advanceBookingSeatFocus(true)}>{skippedBookingSeats[`${activeSeatLeg}:${activeSeatPassenger}`] ? 'Seat already skipped' : 'Skip this passenger seat'}</button><button className="portal-btn portal-btn-outline" onClick={skipAllBookingSeats}>Skip all seat selection</button><button className="portal-btn portal-btn-secondary" onClick={() => advanceBookingSeatFocus(false)} disabled={!selectedBookingSeats[`${activeSeatLeg}:${activeSeatPassenger}`]}>Confirm seat & next passenger <ArrowRight size={15}/></button><button className="portal-btn portal-btn-primary" disabled={!allBookingSeatsSelected} onClick={() => setFlowStep('payment')}>Continue to payment <ArrowRight size={15}/></button></div>
  </section>;

  const renderPayment = () => <section className="portal-flow-panel"><div className="portal-flow-heading"><button className="portal-back-link" onClick={() => setFlowStep('seats')}><ArrowLeft size={15}/> Back to seats</button><span className="portal-eyebrow">STEP 5 · PAYMENT SIMULATION</span><h1>Review your trip</h1><p>No card details are collected. Choose a simulated outcome to test the booking workflow.</p></div><div className="portal-review-layout"><div className="portal-review-main"><div className="portal-review-card"><h3><Plane size={18}/> Itinerary</h3>{[chosenOutbound,...(chosenReturn?[chosenReturn]:[])].filter(Boolean).map((f,i)=><div className="portal-review-flight" key={f.flight_id}><span className="portal-carrier-mark">E</span><div><strong>{i===0?'Outbound':'Return'} · {f.origin.city} to {f.destination.city}</strong><small>{f.flight_number} · {niceDate(f.departure_date)} · {f.departure_time}–{f.arrival_time}</small></div><strong>{money((i===0?outboundFare:returnFare)?.price)}</strong></div>)}</div><div className="portal-review-card"><h3><UserRound size={18}/> Passengers & contact</h3>{passengers.map((p,i)=><div className="portal-review-line" key={i}><span>{p.title} {p.first_name} {p.last_name}</span><small>{p.passenger_type}</small></div>)}<div className="portal-review-line"><span>{contact.email}</span><small>Contact</small></div></div><div className="portal-review-card"><h3><Plane size={18}/> Seat selection</h3>{Object.keys(selectedBookingSeats).length ? Object.entries(selectedBookingSeats).map(([key, seat])=><div className="portal-review-line" key={key}><span>{key.split(':')[0] === 'return' ? 'Return' : 'Outbound'} · Passenger {Number(key.split(':')[1])+1}</span><strong>{seat}</strong></div>) : <div className="portal-review-line"><span>No seats selected</span><small>Selection skipped</small></div>}<button type="button" className="portal-btn portal-btn-outline" onClick={()=>setFlowStep('seats')}>Change seats</button></div><div className="portal-review-card"><h3><CreditCard size={18}/> Payment test</h3><label className={`portal-payment-option ${paymentSimulation==='approve'?'chosen':''}`}><input type="radio" checked={paymentSimulation==='approve'} onChange={()=>setPaymentSimulation('approve')}/><span><strong>Simulate successful payment</strong><small>Creates a synthetic confirmed booking. No money moves.</small></span><CheckCircle2 size={20}/></label><label className={`portal-payment-option ${paymentSimulation==='decline'?'chosen':''}`}><input type="radio" checked={paymentSimulation==='decline'} onChange={()=>setPaymentSimulation('decline')}/><span><strong>Simulate declined payment</strong><small>Tests failure recovery. No booking is created.</small></span><X size={20}/></label><div className="portal-payment-methods"><h3>Choose a payment method</h3>{[{id:'upi',label:'UPI / QR',detail:'Illustrative QR payment flow'},{id:'card',label:'Credit or debit card',detail:'Card payments are not processed'},{id:'netbanking',label:'Net banking',detail:'No bank connection is made'},{id:'wallet',label:'Digital wallet',detail:'Demo-only payment choice'}].map(m=><button type="button" key={m.id} className={`portal-payment-method ${paymentMethod===m.id?'active':''}`} onClick={()=>setPaymentMethod(m.id)}><span className="portal-payment-method-radio">{paymentMethod===m.id?'✓':''}</span><span><strong>{m.label}</strong><small>{m.detail}</small></span></button>)}{paymentMethod==='upi' && <div className="portal-qr-explainer"><div className="portal-fake-qr"><span>DEMO</span></div><div><strong>Demo QR placeholder</strong><p>Not a payable QR. No UPI app or payment provider is launched.</p></div></div>}</div></div></div><aside className="portal-price-summary"><h3>Payment summary</h3><div className="portal-price-row"><span>Flight fares</span><span>{money(fareSubtotal)}</span></div><div className="portal-price-row"><span>Extras</span><span>{money(extraSubtotal)}</span></div><div className="portal-price-row"><span>Taxes & charges</span><span>Included in demo fare</span></div><div className="portal-price-total"><span>Total per booking</span><strong>{money(totalBeforeConfirm)}</strong></div><small className="portal-price-note">Illustrative AED price only. Not a quote or amount payable to Emirates.</small><button className="portal-btn portal-btn-primary portal-full-btn" disabled={busy} onClick={confirmBooking}>{busy?'Processing demo…':paymentSimulation==='approve'?'Confirm demo booking':'Test declined payment'} <ArrowRight size={16}/></button><div className="portal-safe-payment"><LockKeyholeInline/> No card data entered or stored</div></aside></div><div className="portal-form-actions"><button className="portal-btn portal-btn-outline" onClick={()=>setFlowStep('extras')}><ArrowLeft size={15}/> Back</button></div></section>;

  const renderConfirmation = () => <section className="portal-confirm-panel"><div className="portal-confirm-check"><CheckCircle2 size={33}/></div><span className="portal-eyebrow">DEMO BOOKING CONFIRMED</span><h1>Your next journey starts here.</h1><p>This is a synthetic booking confirmation. It is not valid for travel and no payment has been taken.</p>{confirmation && <><div className="portal-pnr-card"><div><small>BOOKING REFERENCE</small><strong>{confirmation.booking_reference}</strong><span>Keep this reference for the demo check-in flow.</span></div><button className="portal-btn portal-btn-outline" onClick={()=>navigator.clipboard?.writeText(confirmation.booking_reference)}><Ticket size={15}/> Copy reference</button></div><div className="portal-confirm-itinerary">{(confirmation.itinerary||[]).map((j,i)=><div className="portal-confirm-leg" key={j.journey_key}><span className="portal-leg-label">{i===0?'OUTBOUND':'RETURN'} · {j.flight_number}</span><div><strong>{j.origin}</strong><span>{j.departure_time}</span><small>{niceDate(j.departure_date)}</small></div><div className="portal-confirm-line"><i/><span>{Math.floor(j.duration_minutes/60)}h {j.duration_minutes%60}m</span><i/></div><div><strong>{j.destination}</strong><span>{j.arrival_time}</span><small>{niceDate(j.arrival_date)}</small></div></div>)}</div><div className="portal-confirm-total"><span>Mock total</span><strong>{money(confirmation.total, confirmation.currency)}</strong><small>Simulated payment · {confirmation.payment_status}</small></div><div className="portal-form-actions centered"><button className="portal-btn portal-btn-outline" onClick={()=>onNavigate('manage')}>Manage this booking</button><button className="portal-btn portal-btn-primary" onClick={()=>onNavigate('checkin',{pnr:confirmation.booking_reference,surname:confirmation.surname})}>Continue to check-in <ArrowRight size={16}/></button></div></>}</section>;

  const renderManage = () => <section className="portal-flow-panel portal-secondary-page"><div className="portal-flow-heading"><span className="portal-eyebrow">YOUR JOURNEY, YOUR WAY</span><h1>Manage your booking</h1><p>Retrieve a synthetic booking to review its itinerary or open the mock web check-in journey.</p></div><form className="portal-manage-form" onSubmit={lookupBooking}><div className="portal-field"><label>Booking reference</label><input required minLength={6} maxLength={8} value={manage.pnr} onChange={e=>setManage(s=>({...s,pnr:e.target.value.toUpperCase()}))} placeholder="e.g. EM8K2Q"/></div><div className="portal-field"><label>Passenger surname</label><input required value={manage.surname} onChange={e=>setManage(s=>({...s,surname:e.target.value.toUpperCase()}))} placeholder="Surname"/></div><button className="portal-btn portal-btn-primary" disabled={busy}>{busy?'Retrieving…':'Retrieve booking'} <ArrowRight size={15}/></button></form>{manageResult && <div className="portal-manage-result"><div className="portal-result-heading"><span className="portal-confirm-check small"><CheckCircle2 size={20}/></span><div><strong>Booking retrieved</strong><small>Reference {manageResult.booking?.record_locator} · Synthetic record</small></div></div><div className="portal-manage-journeys">{(manageResult.booking?.journeys||[]).map(j=><div key={j.journey_key}><span>{j.flight_number||'EK DEMO'}</span><strong>{j.origin} → {j.destination}</strong><small>{niceDate(j.departure_date)} {j.departure_time || ''}</small></div>)}</div><button className="portal-btn portal-btn-primary" onClick={()=>onNavigate('checkin',{pnr:manage.pnr,surname:manage.surname})}>Open web check-in <ArrowRight size={15}/></button></div>}{lastBooking && !manageResult && <div className="portal-inline-notice"><Ticket size={16}/> Most recent local demo booking: {lastBooking.booking_reference} · {lastBooking.surname}</div>}</section>;

  const renderStatus = () => <section className="portal-flow-panel portal-secondary-page"><div className="portal-flow-heading"><span className="portal-eyebrow">TRAVEL INFORMATION</span><h1>Flight status</h1><p>Look up an illustrative status for a demo EK flight number. This does not query a live flight-tracking service.</p></div><form className="portal-status-form" onSubmit={checkFlightStatus}><div className="portal-field"><label>Flight number</label><input required pattern="EK[0-9]{1,4}" maxLength={6} value={flightStatus.number} onChange={e=>setFlightStatus(s=>({...s,number:e.target.value.toUpperCase()}))} placeholder="EK203"/></div><div className="portal-field"><label>Departure date</label><input required type="date" value={flightStatus.date} onChange={e=>setFlightStatus(s=>({...s,date:e.target.value}))}/></div><button className="portal-btn portal-btn-primary" disabled={busy}>{busy?'Checking…':'Check status'} <Search size={16}/></button></form>{flightStatusResult && <div className="portal-status-result"><span className="portal-status-icon"><Plane size={22}/></span><div><span className="portal-eyebrow">{flightStatusResult.flight_number} · {niceDate(flightStatusResult.departure_date)}</span><h2>{flightStatusResult.status}</h2><p>{flightStatusResult.message}</p><small>Demo status only · no live tracker queried</small></div></div>}</section>;

  const renderHelp = () => <section className="portal-flow-panel portal-secondary-page"><div className="portal-flow-heading"><span className="portal-eyebrow">TRAVEL SUPPORT</span><h1>How can we help?</h1><p>Ask about booking steps, check-in, baggage or the demo's terms. Answers are restricted to the available knowledge sources.</p></div><form className="portal-help-search" onSubmit={submitFaq}><HelpCircle size={20}/><input value={faqQuery} required onChange={e=>setFaqQuery(e.target.value)} placeholder="Ask a flight-related question…"/><button className="portal-btn portal-btn-primary">Ask <ArrowRight size={15}/></button></form>{faqResult && <div className="portal-faq-answer"><strong>Travel assistant</strong><p>{faqResult.answer}</p>{(faqResult.citations||[]).map(c=>c.url?.startsWith('https://')?<a key={c.url||c.title} href={c.url} target="_blank" rel="noreferrer">{c.title || 'Source'} ↗</a>:<span key={c.title} className="portal-chat-source-note">{c.title || 'Demo policy'} · local demo source</span>)}</div>}<div className="portal-help-grid">{[{title:'Booking basics',q:'How do I book a flight online?'},{title:'Check-in',q:'What is the online check-in window?'},{title:'Manage a booking',q:'How can I change a booking?'},{title:'Seats & extras',q:'Can I select a seat or add baggage?'}].map(x=><button key={x.title} onClick={()=>{setFaqQuery(x.q); setFaqResult(null);}} className="portal-help-card"><span><HelpCircle size={18}/></span><strong>{x.title}</strong><small>{x.q}</small><ChevronRight size={16}/></button>)}</div></section>;

  let content;
  if (section === 'manage') content = renderManage();
  else if (section === 'status') content = renderStatus();
  else if (section === 'help') content = renderHelp();
  else if (flowStep === 'outbound') content = renderFlights('outbound');
  else if (flowStep === 'return') content = renderFlights('return');
  else if (flowStep === 'passengers') content = renderPassengers();
  else if (flowStep === 'extras') content = renderExtras();
  else if (flowStep === 'seats') content = renderSeats();
  else if (flowStep === 'payment') content = renderPayment();
  else if (flowStep === 'confirmation') content = renderConfirmation();
  else content = renderSearchForm();

  return <div className="portal-shell" onInvalidCapture={e => { const target = e.target; const field = target.closest('.portal-field')?.querySelector('label')?.textContent || target.getAttribute('aria-label') || target.placeholder || target.name || 'required field'; setError(`${field}: ${target.validationMessage || 'Please complete this field in the required format.'}`); }}>
    <div className="portal-unofficial"><ShieldCheck size={13}/> Independent mock demonstration · synthetic fares and bookings only · not an official Emirates website</div>
    <header className="portal-header"><button className="portal-brand" onClick={()=>handleNav('book')}><span className="portal-brand-mark"><img src="/emirates-logo-badge.svg" alt="Emirates" /></span><span><strong>EMIRATES</strong><small>FLIGHT BOOKING DEMO</small></span></button><button className="portal-mobile-menu" onClick={()=>setMobileNavOpen(v=>!v)} aria-label="Toggle navigation">{mobileNavOpen?<X/>:<Menu/>}</button><nav className={`portal-nav ${mobileNavOpen?'open':''}`}>{navItems.map(item=><div className={`portal-nav-group ${activeMenu===item.key?'menu-open':''}`} key={item.key}><button type="button" className={`${section===item.key || (item.key==='book' && section==='book')?'active':''}`} onClick={()=>item.items ? setActiveMenu(old=>old===item.key?null:item.key) : activateNavItem(item)} aria-expanded={item.items ? activeMenu===item.key : undefined}>{item.label}{item.items && <ChevronDown size={13}/>}</button>{item.items && activeMenu===item.key && <div className="portal-nav-dropdown">{item.items.map(child=><button type="button" key={child.label} onClick={()=>activateNavItem(child)}>{child.label}<ChevronRight size={14}/></button>)}</div>}</div>)}</nav><div className="portal-header-tools"><span className="portal-demo-indicator"><i/> DEMO</span><button className="portal-help-link" onClick={()=>handleNav('help')}><HelpCircle size={16}/> Help</button></div></header>
    <div className="portal-main-layout"><main className="portal-main-column">{section==='book' && ['outbound','return','passengers','extras','seats','payment','confirmation'].includes(flowStep) ? renderStepBar() : null}{error && <div className="portal-alert error"><X size={16}/><span>{error}</span><button onClick={()=>setError('')} aria-label="Dismiss"><X size={14}/></button></div>}{notice && <div className="portal-alert info"><ShieldCheck size={15}/><span>{notice}</span><button onClick={()=>setNotice('')} aria-label="Dismiss"><X size={14}/></button></div>}{content}<footer className="portal-footer"><span>© Emirates-inspired flight booking POC</span><span><ShieldCheck size={13}/> No live reservations · no money collected</span><button onClick={()=>handleNav('help')}>Help & FAQ</button></footer></main><SiteAssistant page={section==='book'?flowStep:section} searchForm={searchForm} setSearchForm={setSearchForm} pdpContext={{ passengers, current_passenger_index: 0, contact, accepted_terms: acceptedTerms }} pageContext={{ section, flow_step: flowStep, passengers, contact, selected_extras: selectedExtrasByPassenger, active_extras_passenger: activeExtrasPassenger, selected_seats: selectedBookingSeats, skipped_seats: skippedBookingSeats, is_round_trip: isRoundTrip, active_seat_leg: activeSeatLeg, active_seat_passenger: activeSeatPassenger, payment_method: paymentMethod, payment_simulation: paymentSimulation, accepted_terms: acceptedTerms, manage }} pageError={error} searchResult={searchResult} onAssistantAction={handleAssistantAction} onNavigate={handleNav}/></div>
  </div>;
}

function LockKeyholeInline() { return <ShieldCheck size={14} aria-hidden="true"/>; }
