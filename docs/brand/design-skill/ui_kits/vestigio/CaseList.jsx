/* global React */

const CASES = [
  {id:'2025-0318', title:'Contencioso Fazendário — Obra BR-101', client:'Consórcio Via Norte S.A.',
    msgs:14832, atch:3107, days:62, perito:'R. Nogueira', status:'ok', updated:'2025-03-14'},
  {id:'2025-0291', title:'Due Diligence — Alvo distressed', client:'Vega Capital Partners',
    msgs:41208, atch:9814, days:90, perito:'L. Tavares', status:'warn', updated:'2025-03-12'},
  {id:'2025-0276', title:'Arbitragem CCI — fornecimento industrial', client:'MetalTec Ltda.',
    msgs:8104, atch:1422, days:48, perito:'R. Nogueira', status:'ok', updated:'2025-03-11'},
  {id:'2025-0248', title:'Perícia judicial — obra pública municipal', client:'Procuradoria de SP',
    msgs:22519, atch:5008, days:180, perito:'M. Azevedo', status:'alert', updated:'2025-03-08'},
  {id:'2025-0210', title:'Investigação interna — diretoria', client:'Grupo Orion Holdings',
    msgs:3104, atch:812, days:30, perito:'L. Tavares', status:'muted', updated:'2025-02-28'},
];

const CaseList = ({onOpen}) => {
  const [q, setQ] = React.useState('');
  const list = CASES.filter(c => (c.title+c.client+c.id).toLowerCase().includes(q.toLowerCase()));
  return (
    <div>
      <div style={{display:'flex',alignItems:'baseline',gap:16,marginBottom:6}}>
        <Eyebrow>Módulo 01 · Casos</Eyebrow>
      </div>
      <div style={{display:'flex',alignItems:'flex-end',justifyContent:'space-between',marginBottom:24,borderBottom:'1.5px solid #1A1A1A',paddingBottom:16}}>
        <div>
          <h1 style={{fontFamily:'var(--vst-serif)',fontSize:40,fontWeight:500,margin:'6px 0 2px',lineHeight:1.1}}>
            Casos em perícia
          </h1>
          <div style={{fontSize:14,color:'#4A4A4A'}}>
            {CASES.length} casos ativos · última ingestão em 14 de março de 2025
          </div>
        </div>
        <div style={{display:'flex',gap:10}}>
          <Btn variant="ghost"><IconFilter size={16}/>Filtrar</Btn>
          <Btn variant="primary"><IconFolder size={16}/>Novo caso</Btn>
        </div>
      </div>

      <div style={{display:'flex',alignItems:'center',gap:12,marginBottom:18,padding:'10px 14px',
        background:'#FBF8F2',border:'1px solid rgba(26,26,26,.12)'}}>
        <IconSearch size={16}/>
        <input value={q} onChange={e=>setQ(e.target.value)} placeholder="Buscar por cliente, referência ou título"
          style={{flex:1,border:0,background:'transparent',fontSize:15,outline:'none',fontFamily:'var(--vst-sans)'}}/>
        <Chip>⌘K</Chip>
      </div>

      <table style={{width:'100%',borderCollapse:'collapse',fontSize:14,fontVariantNumeric:'tabular-nums'}}>
        <thead>
          <tr>
            {['Referência','Caso / cliente','Perito','Corpus','Atualizado','Status',''].map((h,i) => (
              <th key={i} style={{textAlign:i===3||i===4?'right':'left',fontSize:10,letterSpacing:'.14em',
                textTransform:'uppercase',color:'#9A9A9A',fontWeight:600,padding:'10px 14px',
                borderBottom:'1.5px solid #1A1A1A'}}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {list.map(c => (
            <tr key={c.id} onClick={()=>onOpen(c)} style={{cursor:'pointer'}}
                onMouseEnter={e=>e.currentTarget.style.background='#EDE7DC'}
                onMouseLeave={e=>e.currentTarget.style.background='transparent'}>
              <td style={{padding:'14px',borderBottom:'1px solid rgba(26,26,26,.12)',fontFamily:'var(--vst-mono)',fontSize:12,color:'#4A4A4A',verticalAlign:'top'}}>
                {c.id}
              </td>
              <td style={{padding:'14px',borderBottom:'1px solid rgba(26,26,26,.12)',verticalAlign:'top'}}>
                <div style={{fontFamily:'var(--vst-serif)',fontSize:17,fontWeight:500,lineHeight:1.3,marginBottom:2}}>{c.title}</div>
                <div style={{fontSize:12,color:'#6E6E6E'}}>{c.client}</div>
              </td>
              <td style={{padding:'14px',borderBottom:'1px solid rgba(26,26,26,.12)',color:'#4A4A4A',verticalAlign:'top'}}>{c.perito}</td>
              <td style={{padding:'14px',borderBottom:'1px solid rgba(26,26,26,.12)',textAlign:'right',fontFamily:'var(--vst-mono)',fontSize:12,color:'#4A4A4A',verticalAlign:'top'}}>
                {c.msgs.toLocaleString('pt-BR')} msgs<br/>
                <span style={{color:'#9A9A9A'}}>{c.atch.toLocaleString('pt-BR')} anexos · {c.days}d</span>
              </td>
              <td style={{padding:'14px',borderBottom:'1px solid rgba(26,26,26,.12)',textAlign:'right',fontFamily:'var(--vst-mono)',fontSize:12,color:'#4A4A4A',verticalAlign:'top'}}>{c.updated}</td>
              <td style={{padding:'14px',borderBottom:'1px solid rgba(26,26,26,.12)',verticalAlign:'top'}}>
                <Tag variant={c.status}>
                  {c.status==='ok'?'Íntegro':c.status==='warn'?'Atenção':c.status==='alert'?'Crítico':'Em processamento'}
                </Tag>
              </td>
              <td style={{padding:'14px',borderBottom:'1px solid rgba(26,26,26,.12)',textAlign:'right',color:'#9A9A9A',verticalAlign:'top'}}>
                <IconChevronR size={16}/>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

window.CaseList = CaseList;
window.CASES = CASES;
