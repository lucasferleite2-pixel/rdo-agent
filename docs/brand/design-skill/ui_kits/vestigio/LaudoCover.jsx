/* global React */

const LaudoCover = ({caseData, onBack}) => {
  const c = caseData || {id:'2025-0318', title:'Contencioso Fazendário — Obra BR-101', client:'Consórcio Via Norte S.A.',
    msgs:14832, atch:3107, days:62, perito:'R. Nogueira'};
  return (
    <div>
      <div style={{marginBottom:18,display:'flex',alignItems:'center',gap:12}}>
        <a href="#" onClick={e=>{e.preventDefault();onBack&&onBack()}} style={{color:'#4A4A4A',fontSize:13}}>← Voltar ao caso</a>
        <div style={{marginLeft:'auto',display:'flex',gap:10}}>
          <Btn variant="ghost" size="sm"><IconDownload size={14}/>PDF assinado</Btn>
          <Btn variant="secondary" size="sm">Imprimir</Btn>
        </div>
      </div>

      {/* Cover page — A4-ish feel */}
      <div style={{background:'#FBF8F2',border:'1px solid rgba(26,26,26,.22)',padding:'72px 80px',maxWidth:820,margin:'0 auto',position:'relative',minHeight:900}}>
        {/* Seal watermark */}
        <div style={{position:'absolute',right:-20,bottom:-20,width:280,height:280,opacity:.15,pointerEvents:'none'}}>
          <img src={(window.__resources && window.__resources.seal) || "../../assets/seal.svg"} width="280" height="280" alt=""/>
        </div>

        {/* Header */}
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',paddingBottom:24,borderBottom:'1px solid #1A1A1A',marginBottom:48}}>
          <div>
            <div style={{fontFamily:'var(--vst-serif)',fontSize:28,fontWeight:500}}>
              Vest<span style={{color:'#6B0F1A'}}>í</span>gio
            </div>
            <div style={{fontSize:10,letterSpacing:'.22em',textTransform:'uppercase',color:'#4A4A4A',fontWeight:600,marginTop:4}}>
              Perícia Digital
            </div>
          </div>
          <div style={{textAlign:'right',fontSize:11,letterSpacing:'.12em',textTransform:'uppercase',color:'#9A9A9A',fontWeight:600,lineHeight:1.8}}>
            LAUDO Nº {c.id}<br/>
            VERSÃO 1.2 · FINAL<br/>
            14 DE MARÇO DE 2025
          </div>
        </div>

        {/* Title */}
        <div style={{marginBottom:56}}>
          <div style={{fontSize:11,letterSpacing:'.22em',textTransform:'uppercase',color:'#6B0F1A',fontWeight:600,marginBottom:14}}>
            Laudo pericial — cronologia auditável
          </div>
          <h1 style={{fontFamily:'var(--vst-serif)',fontSize:52,fontWeight:500,lineHeight:1.1,margin:'0 0 24px',letterSpacing:'-.01em'}}>
            {c.title}
          </h1>
          <p style={{fontFamily:'var(--vst-serif)',fontStyle:'italic',fontSize:20,lineHeight:1.5,color:'#4A4A4A',maxWidth:'40ch',margin:0}}>
            Consolidação cronológica de comunicações digitais entre as partes, acompanhada de cadeia de custódia e hashes SHA-256 por vestígio.
          </p>
        </div>

        {/* Metadata grid */}
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'32px 56px',marginBottom:48,paddingTop:32,borderTop:'1px solid rgba(26,26,26,.22)'}}>
          {[
            ['Solicitante',     c.client],
            ['Processo',        'Autos nº 5012345-67.2024.4.03.6100'],
            ['Perito responsável', `${c.perito} · OAB/SP 148.221`],
            ['Período analisado', '12 jan 2025 — 14 mar 2025'],
            ['Corpus',          `${c.msgs.toLocaleString('pt-BR')} mensagens · ${c.atch.toLocaleString('pt-BR')} anexos`],
            ['Fontes',          'WhatsApp · E-mail · Documentos · Áudio'],
            ['Emitido em',      '14 de março de 2025, São Paulo'],
            ['Assinatura',      'ICP-Brasil · A3 · válida'],
          ].map(([k,v]) => (
            <div key={k} style={{borderBottom:'1px solid rgba(26,26,26,.12)',paddingBottom:10}}>
              <div style={{fontSize:10,letterSpacing:'.14em',textTransform:'uppercase',color:'#9A9A9A',fontWeight:600,marginBottom:4}}>{k}</div>
              <div style={{fontFamily:'var(--vst-serif)',fontSize:16,color:'#1A1A1A',lineHeight:1.3}}>{v}</div>
            </div>
          ))}
        </div>

        {/* Authentication block */}
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-end',gap:40,marginTop:64}}>
          <div style={{flex:1}}>
            <div style={{fontSize:10,letterSpacing:'.14em',textTransform:'uppercase',color:'#9A9A9A',fontWeight:600,marginBottom:8}}>Hash do laudo · SHA-256</div>
            <div style={{fontFamily:'var(--vst-mono)',fontSize:12,color:'#1A1A1A',wordBreak:'break-all',lineHeight:1.5,background:'#F5F1EA',padding:'10px 12px',border:'1px solid rgba(26,26,26,.12)'}}>
              a7f3d29b1e8c4f019b2d7a8e4c1f9d3b5a8e2d7c4f1b9e3a6d8c2f4b1e7a9d3c
            </div>
          </div>
          <div style={{width:120,textAlign:'center'}}>
            <img src={(window.__resources && window.__resources.seal) || "../../assets/seal.svg"} width="100" height="100" alt=""/>
            <div style={{fontSize:9,letterSpacing:'.18em',textTransform:'uppercase',color:'#8B6F47',fontWeight:600,marginTop:4}}>MMXXV</div>
          </div>
        </div>
      </div>
    </div>
  );
};

window.LaudoCover = LaudoCover;
