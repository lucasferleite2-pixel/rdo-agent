/* global React */
const { useState } = React;

// ---------- Icon helper (Lucide inline SVGs, stroke 1.5) ----------
const Icon = ({ d, size = 18, children }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{flexShrink:0}}>
    {children || <path d={d} />}
  </svg>
);
const IconFileText = (p) => <Icon {...p}><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/></Icon>;
const IconFolder   = (p) => <Icon {...p}><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/></Icon>;
const IconClock    = (p) => <Icon {...p}><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></Icon>;
const IconShield   = (p) => <Icon {...p}><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/></Icon>;
const IconSearch   = (p) => <Icon {...p}><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></Icon>;
const IconDownload = (p) => <Icon {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></Icon>;
const IconFilter   = (p) => <Icon {...p}><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></Icon>;
const IconHash     = (p) => <Icon {...p}><line x1="4" y1="9" x2="20" y2="9"/><line x1="4" y1="15" x2="20" y2="15"/><line x1="10" y1="3" x2="8" y2="21"/><line x1="16" y1="3" x2="14" y2="21"/></Icon>;
const IconMsg      = (p) => <Icon {...p}><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></Icon>;
const IconMail     = (p) => <Icon {...p}><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 5L2 7"/></Icon>;
const IconPaperclip= (p) => <Icon {...p}><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.99 8.8l-8.58 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></Icon>;
const IconChevronR = (p) => <Icon {...p}><polyline points="9 18 15 12 9 6"/></Icon>;

// ---------- Buttons ----------
const Btn = ({variant='primary', size='md', children, ...p}) => {
  const styles = {
    padding: size==='sm' ? '6px 12px' : '9px 18px',
    fontSize: size==='sm' ? 12 : 14,
    fontWeight: 500,
    fontFamily: 'var(--vst-sans)',
    borderRadius: 3,
    border: '1px solid transparent',
    cursor: 'pointer',
    letterSpacing: '.01em',
    transition: 'background 120ms, color 120ms, border-color 120ms',
    display: 'inline-flex', alignItems:'center', gap:8,
    ...({
      primary:   { background:'#6B0F1A', color:'#F5F1EA' },
      secondary: { background:'transparent', color:'#1A1A1A', borderColor:'#1A1A1A' },
      ghost:     { background:'transparent', color:'#4A4A4A', borderColor:'rgba(26,26,26,.22)' },
    }[variant])
  };
  return <button style={styles} {...p}>{children}</button>;
};

// ---------- Tag ----------
const Tag = ({variant='muted', children}) => {
  const m = {
    ok:    {c:'#2A3E28', b:'#3D5A3A', bg:'rgba(61,90,58,.08)', d:'#3D5A3A'},
    warn:  {c:'#6B4A12', b:'#A8751C', bg:'rgba(168,117,28,.08)', d:'#A8751C'},
    alert: {c:'#4A0A12', b:'#6B0F1A', bg:'rgba(107,15,26,.08)', d:'#6B0F1A'},
    muted: {c:'#4A4A4A', b:'rgba(26,26,26,.22)', bg:'transparent', d:'#9A9A9A'},
  }[variant];
  return (
    <span style={{fontSize:11,letterSpacing:'.06em',padding:'2px 10px',borderRadius:999,
      border:`1px solid ${m.b}`,background:m.bg,color:m.c,textTransform:'uppercase',
      fontWeight:600,display:'inline-flex',alignItems:'center',gap:6}}>
      <span style={{width:6,height:6,borderRadius:'50%',background:m.d}}/>
      {children}
    </span>
  );
};

// ---------- Chip (mono) ----------
const Chip = ({children}) => (
  <span style={{fontFamily:'var(--vst-mono)',fontSize:11,padding:'2px 8px',
    background:'#EDE7DC',border:'1px solid rgba(26,26,26,.12)',borderRadius:2,color:'#4A4A4A'}}>
    {children}
  </span>
);

// ---------- Eyebrow ----------
const Eyebrow = ({children, style}) => (
  <div style={{fontSize:11,letterSpacing:'.14em',textTransform:'uppercase',
    color:'#9A9A9A',fontWeight:600,...style}}>{children}</div>
);

Object.assign(window, { Icon, IconFileText, IconFolder, IconClock, IconShield,
  IconSearch, IconDownload, IconFilter, IconHash, IconMsg, IconMail,
  IconPaperclip, IconChevronR, Btn, Tag, Chip, Eyebrow });
