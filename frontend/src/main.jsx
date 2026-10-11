import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import BookingPortal from './BookingPortal.jsx';
import CheckInWorkspace from './CheckInWorkspace.jsx';
import './style.css';
import './theme-overrides.css';

function App() {
  const initial = (() => { try { const state = window.history.state; return state?.emiratesDemo ? state : { emiratesDemo: true, page: 'book', checkinSeed: { pnr: '', surname: '' } }; } catch { return { emiratesDemo: true, page: 'book', checkinSeed: { pnr: '', surname: '' } }; } })();
  const [page, setPage] = useState(initial.page || 'book');
  const [checkinSeed, setCheckinSeed] = useState(initial.checkinSeed || { pnr: '', surname: '' });
  const [lastBooking, setLastBooking] = useState(null);

  useEffect(() => {
    try {
      if (!window.history.state?.emiratesDemo) window.history.replaceState({ emiratesDemo: true, page, checkinSeed }, '', window.location.href);
      const onPop = event => {
        const state = event.state;
        if (state?.emiratesDemo) { setPage(state.page || 'book'); setCheckinSeed(state.checkinSeed || { pnr: '', surname: '' }); }
        else { setPage('book'); setCheckinSeed({ pnr: '', surname: '' }); window.history.replaceState({ emiratesDemo: true, page: 'book', checkinSeed: { pnr: '', surname: '' } }, '', window.location.href); }
      };
      window.addEventListener('popstate', onPop);
      return () => window.removeEventListener('popstate', onPop);
    } catch {}
  }, []);

  const navigate = (next, context = {}) => {
    if (next === 'checkin') {
      try { sessionStorage.removeItem('checkin_demo_sid'); sessionStorage.removeItem('checkin_demo_token'); } catch {}
      setCheckinSeed({ pnr: context.pnr || lastBooking?.booking_reference || '', surname: context.surname || lastBooking?.surname || '' });
      setPage('checkin');
      try { window.history.pushState({ emiratesDemo: true, page: 'checkin', checkinSeed: { pnr: context.pnr || lastBooking?.booking_reference || '', surname: context.surname || lastBooking?.surname || '' } }, '', window.location.href); } catch {}
      return;
    }
    setPage(next);
    try { window.history.pushState({ emiratesDemo: true, page: next, checkinSeed }, '', window.location.href); } catch {}
  };

  if (page === 'checkin') {
    return <CheckInWorkspace initialPnr={checkinSeed.pnr} initialSurname={checkinSeed.surname} onExit={() => navigate('book')} />;
  }

  return <BookingPortal section={page} onNavigate={navigate} onBookingCreated={setLastBooking} lastBooking={lastBooking} />;
}

createRoot(document.getElementById('root')).render(<App />);
