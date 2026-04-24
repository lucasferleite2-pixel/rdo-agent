/* global React */
const { useState: useStateShell } = React;

const AppShell = ({active, onNav, children}) => {
  const tabs = [
    {id:'casos',    label:'Casos'},
    {id:'corpus',   label:'Corpus'},
    {id:'laudos',   label:'Laudos'},
    {id:'auditoria',label:'Auditoria'},
  ];
  return (
    <div style={{minHeight:'100vh',background:'#F5F1EA',display:'flex',flexDirection:'column'}}>
      <header style={{background:'#FBF8F2',borderBottom:'1px solid rgba(26,26,26,.22)',borderTop:'1px solid rgba(26,26,26,.22)'}}>
        <div style={{maxWidth:1280,margin:'0 auto',padding:'14px 32px',display:'flex',alignItems:'center',gap:28}}>
          <div style={{fontFamily:'var(--vst-serif)',fontSize:22,fontWeight:500,letterSpacing:'-.005em'}}>
            Vest<span style={{color:'#6B0F1A'}}>í</span>gio
          </div>
          <div style={{width:1,height:18,background:'rgba(26,26,26,.22)'}}/>
          <nav style={{display:'flex',gap:22}}>
            {tabs.map(t => (
              <a key={t.id} href="#" onClick={e=>{e.preventDefault();onNav(t.id)}}
                 style={{fontSize:14,textDecoration:'none',paddingBottom:3,
                   color: active===t.id ? '#1A1A1A':'#4A4A4A',
                   borderBottom: active===t.id ? '1.5px solid #6B0F1A':'1.5px solid transparent'}}>
                {t.label}
              </a>
            ))}
          </nav>
          <div style={{marginLeft:'auto',display:'flex',alignItems:'center',gap:14,fontSize:13,color:'#4A4A4A'}}>
            <span>R. Nogueira</span>
            <Chip>OAB/SP 148.221</Chip>
          </div>
        </div>
      </header>
      <main style={{flex:1,maxWidth:1280,margin:'0 auto',padding:'32px',width:'100%',boxSizing:'border-box'}}>
        {children}
      </main>
      <footer style={{borderTop:'1px solid rgba(26,26,26,.22)',padding:'14px 32px',
        fontFamily:'var(--vst-mono)',fontSize:11,color:'#9A9A9A',display:'flex',gap:18,justifyContent:'center'}}>
        <span>Vestígio v2.4.1</span>
        <span>·</span>
        <span>HCF Investimentos e Participações</span>
        <span>·</span>
        <span>Última auditoria 2025-03-14</span>
      </footer>
    </div>
  );
};

window.AppShell = AppShell;
