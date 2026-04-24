/* global React */

const VESTIGIOS = [
  {id:'vst_8f3a91b2', date:'2025-03-14T09:42:17-03:00', src:'WhatsApp', from:'+55 11 9•••4821',
    kind:'msg', title:'Confirmação do aditivo nº 14',
    excerpt:'Confirmo recebimento do aditivo. Assinatura prevista para sexta-feira, 21.',
    hash:'8f3a91b2e7c4…d109', atch:1, status:'ok'},
  {id:'vst_c410b7a2', date:'2025-03-12T17:08:44-03:00', src:'E-mail', from:'compras@consorcio.br',
    kind:'mail', title:'Proposta comercial revisada — v3',
    excerpt:'Segue proposta revisada conforme alinhado em call. Thread de 11 respostas encadeadas.',
    hash:'c410b7a2f9…a2ff', atch:2, status:'ok'},
  {id:'vst_5e9d12ab', date:'2025-03-10T14:22:09-03:00', src:'Documento', from:'ata-obra-38.docx',
    kind:'doc', title:'Ata de obra — 38ª semana',
    excerpt:'Reunião de acompanhamento semanal. Pendências: liberação ambiental do trecho 4.',
    hash:'5e9d12ab47…7b01', atch:0, status:'warn'},
  {id:'vst_2a8f4433', date:'2025-03-07T10:00:00-03:00', src:'Áudio', from:'reuniao-diretoria.ogg',
    kind:'audio', title:'Gravação · reunião de diretoria',
    excerpt:'Transcrição automática. 47 min. Speakers identificados: A, B, C.',
    hash:'2a8f443312…3e9c', atch:1, status:'ok'},
  {id:'vst_0b41e2cc', date:'2025-03-05T22:14:06-03:00', src:'WhatsApp', from:'+55 11 9•••2101',
    kind:'msg', title:'Negociação noturna — alteração de prazo',
    excerpt:'Preciso que você autorize a extensão do prazo. Falo com o jurídico amanhã cedo.',
    hash:'0b41e2cc85…f221', atch:0, status:'alert'},
  {id:'vst_7d33fa01', date:'2025-03-02T08:30:52-03:00', src:'E-mail', from:'juridico@parteb.com',
    kind:'mail', title:'Notificação extrajudicial — envio formal',
    excerpt:'Encaminhamos a notificação formal conforme cláusula 12.3 do contrato-base.',
    hash:'7d33fa0112…c804', atch:3, status:'ok'},
];

const srcIcon = (kind) => {
  const p = {width:16,height:16};
  return kind==='msg'?<IconMsg {...p}/>:kind==='mail'?<IconMail {...p}/>:
         kind==='audio'?<IconPaperclip {...p}/>:<IconFileText {...p}/>;
};

const Timeline = () => {
  const [filter, setFilter] = React.useState('all');
  const list = filter==='all'?VESTIGIOS:VESTIGIOS.filter(v=>v.kind===filter);
  const filters = [
    {id:'all',label:'Todos',n:VESTIGIOS.length},
    {id:'msg',label:'Mensagens',n:VESTIGIOS.filter(v=>v.kind==='msg').length},
    {id:'mail',label:'E-mails',n:VESTIGIOS.filter(v=>v.kind==='mail').length},
    {id:'doc',label:'Documentos',n:VESTIGIOS.filter(v=>v.kind==='doc').length},
    {id:'audio',label:'Áudios',n:VESTIGIOS.filter(v=>v.kind==='audio').length},
  ];

  return (
    <div style={{display:'grid',gridTemplateColumns:'240px 1fr',gap:40}}>
      {/* Filter rail */}
      <aside>
        <Eyebrow style={{marginBottom:12,paddingBottom:8,borderBottom:'1px solid rgba(26,26,26,.22)'}}>Filtros</Eyebrow>
        <div style={{display:'flex',flexDirection:'column',gap:2}}>
          {filters.map(f => (
            <button key={f.id} onClick={()=>setFilter(f.id)}
              style={{background:filter===f.id?'#EDE7DC':'transparent',border:0,borderLeft:filter===f.id?'2px solid #6B0F1A':'2px solid transparent',
                padding:'8px 12px',textAlign:'left',cursor:'pointer',display:'flex',justifyContent:'space-between',
                fontFamily:'var(--vst-sans)',fontSize:14,color:filter===f.id?'#1A1A1A':'#4A4A4A'}}>
              <span>{f.label}</span>
              <span style={{fontFamily:'var(--vst-mono)',fontSize:11,color:'#9A9A9A'}}>{f.n}</span>
            </button>
          ))}
        </div>
        <Eyebrow style={{marginTop:28,marginBottom:12,paddingBottom:8,borderBottom:'1px solid rgba(26,26,26,.22)'}}>Período</Eyebrow>
        <div style={{fontSize:13,color:'#4A4A4A',lineHeight:1.6}}>
          <div>De: <span style={{fontFamily:'var(--vst-mono)',fontSize:12}}>2025-01-12</span></div>
          <div>Até: <span style={{fontFamily:'var(--vst-mono)',fontSize:12}}>2025-03-14</span></div>
          <div style={{color:'#9A9A9A',marginTop:4,fontSize:12}}>62 dias · 14.832 vestígios</div>
        </div>
      </aside>

      {/* Timeline */}
      <div>
        <div style={{borderLeft:'1.5px solid #1A1A1A',paddingLeft:24,marginLeft:10}}>
          {list.map(v => (
            <article key={v.id} style={{position:'relative',padding:'18px 0',borderBottom:'1px solid rgba(26,26,26,.12)'}}>
              <span style={{position:'absolute',left:-31,top:24,width:11,height:11,borderRadius:'50%',
                background:'#F5F1EA',border:`1.5px solid ${v.status==='alert'?'#6B0F1A':v.status==='warn'?'#A8751C':'#1A1A1A'}`}}/>
              <div style={{display:'flex',justifyContent:'space-between',alignItems:'baseline',gap:16,marginBottom:6}}>
                <div style={{display:'flex',alignItems:'center',gap:10,color:'#4A4A4A'}}>
                  {srcIcon(v.kind)}
                  <span style={{fontFamily:'var(--vst-mono)',fontSize:12}}>{v.date.replace('T',' · ').slice(0,22)}</span>
                  <span style={{color:'#9A9A9A'}}>·</span>
                  <span style={{fontSize:13}}>{v.src}</span>
                  <span style={{color:'#9A9A9A'}}>·</span>
                  <span style={{fontSize:13,fontFamily:'var(--vst-mono)'}}>{v.from}</span>
                </div>
                <Chip>{v.id}</Chip>
              </div>
              <h3 style={{fontFamily:'var(--vst-serif)',fontSize:20,fontWeight:500,margin:'0 0 6px',lineHeight:1.3}}>
                {v.title}
              </h3>
              <p style={{fontSize:15,lineHeight:1.6,color:'#1A1A1A',margin:'0 0 10px',maxWidth:'68ch'}}>
                «{v.excerpt}»
              </p>
              <div style={{display:'flex',gap:10,alignItems:'center',fontSize:12,color:'#9A9A9A',fontFamily:'var(--vst-mono)'}}>
                <IconHash size={13}/>
                <span>sha256 {v.hash}</span>
                {v.atch>0 && <><span>·</span><IconPaperclip size={13}/><span>{v.atch} anexo{v.atch>1?'s':''}</span></>}
                {v.status==='alert' && <><span>·</span><Tag variant="alert">Rever fonte</Tag></>}
                {v.status==='warn' && <><span>·</span><Tag variant="warn">Metadado inferido</Tag></>}
              </div>
            </article>
          ))}
        </div>
      </div>
    </div>
  );
};

window.Timeline = Timeline;
