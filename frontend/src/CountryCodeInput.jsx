import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

let registryPromise = null;
function loadRegistry(apiBase) {
  if (!registryPromise) {
    registryPromise = fetch(`${apiBase}/api/v1/countries?limit=300`)
      .then(async response => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Country registry could not be loaded.');
        return Array.isArray(payload.countries) ? payload.countries : [];
      })
      .catch(error => { registryPromise = null; throw error; });
  }
  return registryPromise;
}
const normal = value => String(value || '').trim().normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase();

function findCountry(registry, value) {
  const input = normal(value).replace(/[().,]/g, '').replace(/\s+/g, ' ');
  if (!input) return null;
  return registry.find(country => [country.alpha_2, country.alpha_3].some(code => normal(code) === input))
    || registry.find(country => [country.name, country.official_name].some(name => normal(name) === input))
    || null;
}

export default function CountryCodeInput({ value, onChange, apiBase = 'http://localhost:8000', label = 'Country', required = true, placeholder = 'Type country name, 2-letter or 3-letter ISO code', id, className = '' }) {
  const [registry, setRegistry] = useState([]);
  const [draft, setDraft] = useState(String(value || ''));
  const [open, setOpen] = useState(false);
  const [focused, setFocused] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef(null);

  useEffect(() => {
    let active = true;
    loadRegistry(apiBase).then(items => { if (active) setRegistry(items); }).catch(() => { if (active) setError('Country list could not be loaded. Check the API connection or enter a valid ISO code.'); });
    return () => { active = false; };
  }, [apiBase]);

  useEffect(() => {
    if (focused) return;
    const selected = findCountry(registry, value);
    if (selected) {
      setDraft(selected.name);
      setError('');
      if (String(value || '').toUpperCase() !== selected.alpha_2) onChange?.(selected.alpha_2);
    } else {
      setDraft(String(value || ''));
    }
  }, [value, registry, focused, onChange]);

  const suggestions = useMemo(() => {
    const term = normal(draft);
    if (!open || !term) return [];
    const hits = registry.filter(country => [country.name, country.official_name, country.alpha_2, country.alpha_3].some(item => normal(item).includes(term)));
    return hits.sort((a, b) => {
      const exactCodeA = [a.alpha_2, a.alpha_3].some(code => normal(code) === term) ? 0 : 1;
      const exactCodeB = [b.alpha_2, b.alpha_3].some(code => normal(code) === term) ? 0 : 1;
      const prefixA = [a.name, a.official_name, a.alpha_2, a.alpha_3].some(item => normal(item).startsWith(term)) ? 0 : 1;
      const prefixB = [b.name, b.official_name, b.alpha_2, b.alpha_3].some(item => normal(item).startsWith(term)) ? 0 : 1;
      return exactCodeA - exactCodeB || prefixA - prefixB || a.name.localeCompare(b.name);
    }).slice(0, 8);
  }, [registry, draft, open]);

  const choose = useCallback(country => {
    setDraft(country.name);
    setError(''); setOpen(false); setFocused(false);
    onChange?.(country.alpha_2);
  }, [onChange]);

  const commit = useCallback(() => {
    const selected = findCountry(registry, draft);
    setOpen(false); setFocused(false);
    if (selected) {
      setDraft(selected.name); setError(''); onChange?.(selected.alpha_2);
    } else if (!String(draft || '').trim() && !required) {
      setError(''); onChange?.('');
    } else {
      setError('Choose a country from the suggestions or enter its valid ISO-2 / ISO-3 code or full name.');
      onChange?.(String(draft || '').trim());
    }
  }, [registry, draft, required, onChange]);

  return <div className={`country-input-wrap ${className}`}>
    {label ? <label htmlFor={id}>{label}</label> : null}
    <input
      id={id} ref={inputRef} type="text" value={draft} required={required}
      autoComplete="off" spellCheck="false" maxLength={120}
      placeholder={placeholder} aria-autocomplete="list" aria-expanded={open && suggestions.length > 0}
      aria-invalid={Boolean(error)} aria-describedby={error ? `${id || 'country'}-error` : undefined}
      onFocus={() => { setFocused(true); setOpen(true); }}
      onChange={event => { const next = event.target.value; setDraft(next); setError(''); setOpen(true); onChange?.(next); }}
      onBlur={commit}
      onKeyDown={event => {
        if (event.key === 'Escape') { setOpen(false); return; }
        if (event.key === 'Enter' && open && suggestions.length) { event.preventDefault(); choose(suggestions[0]); }
      }}
    />
    {open && suggestions.length > 0 && <div className="country-suggestions" role="listbox" aria-label={`${label || 'Country'} suggestions`}>
      {suggestions.map(country => <button type="button" role="option" key={country.alpha_2} onMouseDown={event => event.preventDefault()} onClick={() => choose(country)}>
        <span>{country.name}</span><small>{country.alpha_2} · {country.alpha_3}</small>
      </button>)}
    </div>}
    {error && <small className="country-input-error" id={`${id || 'country'}-error`}>{error}</small>}
    {!error && String(value || '').trim() && findCountry(registry, value) && <small className="country-input-hint">{findCountry(registry, value).name} · ISO {findCountry(registry, value).alpha_2} / {findCountry(registry, value).alpha_3}</small>}
  </div>;
}

export async function countryDisplayName(apiBase, value) {
  const raw = String(value || '').trim();
  if (!raw || raw === 'Not entered') return raw || 'Not entered';
  try {
    const countries = await loadRegistry(apiBase);
    const country = findCountry(countries, raw);
    return country ? country.name : raw;
  } catch { return raw; }
}
