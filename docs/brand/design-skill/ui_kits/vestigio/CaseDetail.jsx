/* global React */

const CaseDetail = ({caseData, onBack}) => {
  const [tab, setTab] = React.useState('cronologia');
  const c = caseData;

  return (
    <div>
      {/* Breadcrumb + back */}
      <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:14,fontSize:13,color:'#4A4A4A'}}>
        <a href="#" onClick={e=>{e.preventDefault();onBack()}} style={{color:'#4A4A4A'}}>Casos</a>
        <span style={{color:'#9A9A9A'}}>·</span>
        <span style={{fontFamily:'var(--vst-mono)',fontSize:12}}>{c.id}</span>
      </div>

      {/* Case header */}
      <div style={{borderBottom:'1.5px solid #1A1A1A',paddingBottom:18,marginBottom:24,display:'grid',gridTemplateColumns:'1fr 320px',gap:40}}>
        <div>
          <Eyebrow style={{marginBottom:10}}>Caso · {c.id} · {c.client}</Eyebrow>
          <h1 style={{fontFamily:'var(--vst-serif)',fontSize:38,fontWeight:500,lineHeight:1.15,margin:'0 0 10px',maxWidth:'22ch'}}>
            {c.title}
          </h1>
          <div style={{display:'flex',gap:14,color:'#4A4A4A',fontSize:14}}>
            <span>Perito: <strong style={{color:'#1A1A1A',fontWeight:500}}>{c.perito}</strong></span>
            <span>·</span>
            <span>Aberto em 2025-01-12</span>
            <span>·</span>
            <span>Atualizado {c.updated}</span>
          </div>
        </div>
        <div style={{borderLeft:'1px solid rgba(26,26,26,.22)',paddingLeft:24}}>
          <Eyebrow style={{marginBottom:12}}>Corpus</Eyebrow>
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14}}>
            {[
              ['Mensagens', c.msgs.toLocaleString('pt-BR')],
              ['Anexos', c.atch.toLocaleString('pt-BR')],
              ['Período', `${c.days} dias`],
              ['Fontes', '4 canais'],
            ].map(([k,v]) => (
              <div key={k}>
                <div style={{fontSize:11,letterSpacing:'.08em',textTransform:'uppercase',color:'#9A9A9A',fontWeight:600,marginBottom:2}}>{k}</div>
                <div style={{fontFamily:'var(--vst-serif)',fontSize:24,fontWeight:500,lineHeight:1}}>{v}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Tabs + actions */}
      <div style={{display:'flex',alignItems:'flex-end',justifyContent:'space-between',marginBottom:24,borderBottom:'1px solid rgba(26,26,26,.22)'}}>
        <div style={{display:'flex',gap:2}}>
          {[
            {id:'cronologia',label:'Cronologia'},
            {id:'vestigios',label:'Vestígios'},
            {id:'entidades',label:'Entidades'},
            {id:'laudos',label:'Laudos'},
            {id:'custodia',label:'Cadeia de custódia'},
          ].map(t => (
            <button key={t.id} onClick={()=>setTab(t.id)}
              style={{background:'transparent',border:0,padding:'10px 16px',cursor:'pointer',
                fontFamily:'var(--vst-sans)',fontSize:14,fontWeight:tab===t.id?600:500,
                color:tab===t.id?'#1A1A1A':'#6E6E6E',
                borderBottom:tab===t.id?'2px solid #6B0F1A':'2px solid transparent',
                marginBottom:-1}}>{t.label}</button>
          ))}
        </div>
        <div style={{display:'flex',gap:10,paddingBottom:10}}>
          <Btn variant="ghost" size="sm"><IconDownload size={14}/>Exportar</Btn>
          <Btn variant="primary" size="sm"><IconFileText size={14}/>Gerar laudo</Btn>
        </div>
      </div>

      {tab==='cronologia' && <Timeline/>}
      {tab==='vestigios' && (
        <div style={{padding:60,textAlign:'center',color:'#9A9A9A',fontSize:14,border:'1px dashed rgba(26,26,26,.22)'}}>
          Vista em grade dos vestígios — em construção neste protótipo.
        </div>
      )}
      {tab==='entidades' && (
        <div style={{padding:60,textAlign:'center',color:'#9A9A9A',fontSize:14,border:'1px dashed rgba(26,26,26,.22)'}}>
          Mapa de entidades (NER) — pessoas, organizações, valores, datas.
        </div>
      )}
      {tab==='laudos' && <LaudoList caseData={c}/>}
      {tab==='custodia' && (
        <div style={{padding:60,textAlign:'center',color:'#9A9A9A',fontSize:14,border:'1px dashed rgba(26,26,26,.22)'}}>
          Log imutável de operações sobre o corpus.
        </div>
      )}
    </div>
  );
};

const LaudoList = ({caseData}) => (
  <div>
    {[
      {v:'v1.2', date:'2025-03-14', pages:84, status:'ok',     title:'Laudo consolidado — cronologia completa'},
      {v:'v1.1', date:'2025-03-08', pages:72, status:'muted',  title:'Laudo parcial — recorte primeiro trimestre'},
      {v:'v1.0', date:'2025-02-22', pages:48, status:'muted',  title:'Laudo preliminar — entrega contraditório'},
    ].map((l,i) => (
      <div key={i} style={{display:'grid',gridTemplateColumns:'60px 1fr 120px 100px 140px',gap:24,
        padding:'18px 0',borderBottom:'1px solid rgba(26,26,26,.12)',alignItems:'center'}}>
        <div style={{fontFamily:'var(--vst-mono)',fontSize:13,color:'#4A4A4A'}}>{l.v}</div>
        <div>
          <div style={{fontFamily:'var(--vst-serif)',fontSize:18,fontWeight:500}}>{l.title}</div>
          <div style={{fontSize:12,color:'#9A9A9A',marginTop:2}}>Assinatura digital ICP-Brasil · {l.date}</div>
        </div>
        <div style={{fontFamily:'var(--vst-mono)',fontSize:12,color:'#4A4A4A'}}>{l.pages} pp.</div>
        <Tag variant={l.status}>{l.status==='ok'?'Final':'Histórico'}</Tag>
        <div style={{display:'flex',gap:8,justifyContent:'flex-end'}}>
          <Btn variant="ghost" size="sm"><IconDownload size={13}/>PDF</Btn>
          {l.status==='ok' && <Btn variant="secondary" size="sm">Abrir</Btn>}
        </div>
      </div>
    ))}
  </div>
);

window.CaseDetail = CaseDetail;
