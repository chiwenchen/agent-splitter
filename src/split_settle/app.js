import { h, render } from 'preact';
import { useState } from 'preact/hooks';
import { html } from 'htm/preact';
import Avatar from 'boring-avatars';

const avatarColors = ['#1a2a5a','#3a5a9a','#7aa0d0','#b0c8e8','#e0e8f0'];

const i18n = {
  en: {
    title:'SplitSettle', subtitle:'Split expenses instantly. No registration needed.',
    participants:'Participants', addName:'Add a name...', expenses:'Expenses',
    addExpense:'+ Add Expense', description:'Description (optional)', amount:'Amount',
    splitAmong:'Split among', add:'Add', cancel:'Cancel',
    settlement:'Settlement', owes:'→', total:'total',
    transfer:'transfer', transfers:'transfers', toSettle:'to settle',
    shareResults:'Share Results', generating:'Generating...',
    linkCreated:'Link created!', copyLink:'Copy Link', share:'Share',
    validFor:'Valid for 30 days', allSettled:'Everyone is settled up!',
    expense:'Expense', paid:'paid', splitWays:'ways', lang:'EN',
  },
  'zh-TW': {
    title:'SplitSettle', subtitle:'秒算分帳，免註冊、免下載',
    participants:'參加者', addName:'輸入名字...', expenses:'帳單',
    addExpense:'+ 新增帳單', description:'說明（選填）', amount:'金額',
    splitAmong:'分給誰', add:'新增', cancel:'取消',
    settlement:'結算', owes:'→', total:'總計',
    transfer:'筆轉帳', transfers:'筆轉帳', toSettle:'即可結清',
    shareResults:'分享結果', generating:'產生中...',
    linkCreated:'連結已產生！', copyLink:'複製連結', share:'分享',
    validFor:'30 天內有效', allSettled:'全部結清！不用轉帳',
    expense:'消費', paid:'墊付', splitWays:'人分', lang:'中',
  },
  ja: {
    title:'SplitSettle', subtitle:'割り勘を即計算。登録不要。',
    participants:'参加者', addName:'名前を入力...', expenses:'支出',
    addExpense:'+ 支出を追加', description:'説明（任意）', amount:'金額',
    splitAmong:'割り勘メンバー', add:'追加', cancel:'キャンセル',
    settlement:'精算', owes:'→', total:'合計',
    transfer:'件の送金', transfers:'件の送金', toSettle:'で精算完了',
    shareResults:'結果をシェア', generating:'生成中...',
    linkCreated:'リンクを作成しました！', copyLink:'リンクをコピー', share:'シェア',
    validFor:'30日間有効', allSettled:'全員精算済み！',
    expense:'支出', paid:'が支払い', splitWays:'人で割り勘', lang:'JA',
  },
};

function detectLang() {
  const saved = localStorage.getItem('ss_lang');
  if (saved && i18n[saved]) return saved;
  const nav = (navigator.language || '').toLowerCase();
  if (nav.startsWith('zh')) return 'zh-TW';
  if (nav.startsWith('ja')) return 'ja';
  return 'en';
}

function Av({name, size}) {
  return html`<div class="av" style="width:${size||28}px;height:${size||28}px">
    <${Avatar} name=${name} variant="beam" size=${size||28} colors=${avatarColors} />
  </div>`;
}

function ArrowIcon() {
  return html`<svg class="arrow-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
    <path d="M9 5l7 7-7 7" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}

function SlideConfirm({label, onConfirm, disabled}) {
  const [dragging, setDragging] = useState(false);
  const [x, setX] = useState(0);
  const [done, setDone] = useState(false);
  const trackRef = {current: null};
  const btnW = 140;

  function getMax() {
    const track = trackRef.current;
    return track ? track.offsetWidth - btnW - 12 : 200;
  }

  function start(clientX) {
    if (disabled || done) return;
    setDragging(true);
  }

  function move(clientX) {
    if (!dragging) return;
    const track = trackRef.current;
    if (!track) return;
    const rect = track.getBoundingClientRect();
    const newX = Math.max(0, Math.min(clientX - rect.left - btnW/2 - 6, getMax()));
    setX(newX);
  }

  function end() {
    if (!dragging) return;
    setDragging(false);
    if (x >= getMax() * 0.75) {
      setX(getMax());
      setDone(true);
      onConfirm && onConfirm();
    } else {
      setX(0);
    }
  }

  function reset() { setDone(false); setX(0); }

  const progress = getMax() > 0 ? x / getMax() : 0;

  return html`
    <div class="confirm" ref=${el => trackRef.current = el}
      onMouseMove=${e => move(e.clientX)} onMouseUp=${end} onMouseLeave=${end}
      onTouchMove=${e => move(e.touches[0].clientX)} onTouchEnd=${end}>
      <div class="confirm-bg" style="opacity:${progress}"></div>
      <button class="confirm-btn" style="transform:translateX(${x}px);cursor:${done?'default':'grab'}"
        onMouseDown=${e => start(e.clientX)}
        onTouchStart=${e => start(e.touches[0].clientX)}
        disabled=${disabled}>
        ${done ? '✓' : label}
      </button>
      ${!done ? html`<div class="confirm-arrows" style="opacity:${1 - progress}">
        <${ArrowIcon}/><${ArrowIcon}/><${ArrowIcon}/><${ArrowIcon}/><${ArrowIcon}/>
      </div>` : ''}
    </div>
  `;
}

function splitSettle(participants, expenses, currency) {
  if (participants.length < 2 || expenses.length === 0) return null;
  const pSet = new Set(participants);
  const paid = Object.fromEntries(participants.map(p => [p, 0]));
  const owed = Object.fromEntries(participants.map(p => [p, 0]));
  let total = 0;
  for (const e of expenses) {
    if (!pSet.has(e.paid_by) || e.amount <= 0 || e.split_among.length === 0) continue;
    const cents = Math.round(e.amount * 100);
    total += cents; paid[e.paid_by] += cents;
    const share = Math.floor(cents / e.split_among.length);
    const rem = cents % e.split_among.length;
    e.split_among.forEach((p, i) => { if (pSet.has(p)) owed[p] += share + (i < rem ? 1 : 0); });
  }
  const bal = Object.fromEntries(participants.map(p => [p, paid[p] - owed[p]]));
  const creds = participants.filter(p => bal[p] > 0).map(p => [bal[p], p]).sort((a,b) => b[0]-a[0]);
  const debts = participants.filter(p => bal[p] < 0).map(p => [-bal[p], p]).sort((a,b) => b[0]-a[0]);
  const settlements = []; let i = 0, j = 0;
  while (i < creds.length && j < debts.length) {
    const t = Math.min(creds[i][0], debts[j][0]);
    settlements.push({ from: debts[j][1], to: creds[i][1], amount: t / 100 });
    creds[i][0] -= t; debts[j][0] -= t;
    if (creds[i][0] === 0) i++; if (debts[j][0] === 0) j++;
  }
  return { currency, total: total/100, settlements,
           summary: participants.map(p => ({ name: p, paid: paid[p]/100, owed: owed[p]/100, balance: bal[p]/100 })) };
}

const langOrder = ['en', 'zh-TW', 'ja'];

function App() {
  const [lang, setLang] = useState(detectLang());
  const [participants, setP] = useState(['']);
  const [expenses, setE] = useState([]);
  const [currency, setCurrency] = useState(localStorage.getItem('ss_currency') || 'TWD');
  const [newName, setNewName] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [formDesc, setFormDesc] = useState('');
  const [formAmt, setFormAmt] = useState('');
  const [formPayer, setFormPayer] = useState('');
  const [formSplit, setFormSplit] = useState([]);
  const [shareUrl, setShareUrl] = useState('');
  const [sharing, setSharing] = useState(false);
  const [error, setError] = useState('');
  const [addHint, setAddHint] = useState(false);
  const [firstExpenseHint, setFirstExpenseHint] = useState(true);
  const t = i18n[lang];
  const names = participants.filter(p => p.trim());
  const result = splitSettle(names, expenses, currency);
  const nSett = result ? result.settlements.length : 0;

  function cycleLang() { const i=(langOrder.indexOf(lang)+1)%langOrder.length; const n=langOrder[i]; setLang(n); localStorage.setItem('ss_lang',n); }
  function addName() { if(!newName.trim()||names.includes(newName.trim()))return; setP([...participants.filter(p=>p.trim()),newName.trim(),'']); setNewName(''); }
  function removeName(n) { setP(participants.filter(p=>p!==n)); setE(expenses.filter(e=>e.paid_by!==n&&!e.split_among.includes(n))); }
  function openForm() { setFormDesc('');setFormAmt('');setFormPayer(names[0]||'');setFormSplit([...names]);setShowForm(true); }
  function addExpense() { const a=parseFloat(formAmt); if(!a||a<=0||!formPayer||formSplit.length===0)return; setE([...expenses,{description:formDesc||'',paid_by:formPayer,amount:a,split_among:[...formSplit]}]); setShowForm(false); setAddHint(true); setTimeout(()=>setAddHint(false),1500); }
  function removeExpense(i) { setE(expenses.filter((_,idx)=>idx!==i)); setShareUrl(''); }
  function changeCurrency(c) { setCurrency(c); localStorage.setItem('ss_currency',c); }
  function toggleSplit(n) { setFormSplit(formSplit.includes(n)?formSplit.filter(x=>x!==n):[...formSplit,n]); }

  async function share() {
    if(!result||nSett===0)return; setSharing(true);setError('');setShareUrl('');
    try {
      const body={currency,participants:names,expenses:expenses.map(e=>({description:e.description,paid_by:e.paid_by,amount:e.amount,split_among:e.split_among}))};
      const res=await fetch('/v1/share',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      if(!res.ok){const d=await res.json().catch(()=>({}));throw new Error(d.error||'Failed');}
      const data=await res.json(); setShareUrl(window.location.origin+data.url);
    } catch(e){setError(e.message);} setSharing(false);
  }
  async function copyLink(){try{await navigator.clipboard.writeText(shareUrl)}catch(e){}}
  function webShare(){if(navigator.share)navigator.share({title:'SplitSettle',text:t.shareResults,url:shareUrl})}

  return html`
    <div class="header-row">
      <div><h1>${t.title}</h1><div class="subtitle">${t.subtitle}</div></div>
      <button class="lang-btn" onClick=${cycleLang}>${t.lang}</button>
    </div>

    <div class="section">
      <div class="section-title">${t.participants}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        ${names.map(n => html`
          <span class="chip" key=${n}>
            <${Av} name=${n} size=${26} />
            ${n}
            <button onClick=${()=>removeName(n)}>x</button>
          </span>
        `)}
      </div>
      <div class="row" style="margin-top:10px">
        <input placeholder=${t.addName} value=${newName} onInput=${e=>setNewName(e.target.value)}
          onKeyDown=${e=>{if(e.key==='Enter'&&!e.isComposing&&!e.nativeEvent?.isComposing)addName()}} />
        <button class="btn-outline" style="flex:none;padding:10px 16px;width:auto" onClick=${addName}>+</button>
      </div>
    </div>

    <div class="section">
      <div class="row" style="margin-bottom:10px">
        <div class="section-title" style="flex:1;margin:0;line-height:28px">${t.expenses}</div>
        <select style="flex:none;width:80px" value=${currency} onChange=${e=>changeCurrency(e.target.value)}>
          <option>TWD</option><option>USD</option><option>JPY</option><option>EUR</option>
          <option>GBP</option><option>CNY</option><option>KRW</option><option>THB</option>
        </select>
      </div>
      ${expenses.map((e,i) => html`
        <div class="expense-card ${i===expenses.length-1?'expense-card-new':''}" key=${i}>
          <div class="left">
            <${Av} name=${e.paid_by} size=${34} />
            <div>
              <div class="desc">${e.description||t.expense}</div>
              <div class="meta"><span class="tag tag-paid">${e.paid_by} ${t.paid}</span> <span class="tag tag-split">${e.split_among.length} ${t.splitWays}</span></div>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:10px">
            <span class="amount">${currency} ${e.amount.toLocaleString()}</span>
            <button onClick=${()=>removeExpense(i)}>x</button>
          </div>
        </div>
      `)}
      ${showForm ? html`
        <div class="add-form">
          <input placeholder=${t.description} value=${formDesc} onInput=${e=>setFormDesc(e.target.value)} style="margin-bottom:8px" />
          <input placeholder=${t.amount} inputmode="decimal" value=${formAmt} onInput=${e=>setFormAmt(e.target.value)} style="margin-bottom:8px" />
          <select value=${formPayer} onChange=${e=>setFormPayer(e.target.value)} style="margin-bottom:8px">
            ${names.map(n=>html`<option key=${n} value=${n}>${n} ${t.paid}</option>`)}
          </select>
          <div class="section-title" style="margin-top:4px">${t.splitAmong}</div>
          <div class="checkbox-group">
            ${names.map(n=>html`<label key=${n}><input type="checkbox" checked=${formSplit.includes(n)} onChange=${()=>toggleSplit(n)} />${n}</label>`)}
          </div>
          <div class="row" style="margin-top:10px">
            <button class="btn" onClick=${addExpense}>${t.add}</button>
            <button class="btn-outline" onClick=${()=>setShowForm(false)}>${t.cancel}</button>
          </div>
        </div>
      ` : html`<button class="btn-outline ${names.length>=2?'btn-add-hint':''}"
                  onClick=${openForm} disabled=${names.length<2}>${t.addExpense}</button>`}
    </div>

    ${result && nSett > 0 ? html`
      <hr class="divider" />
      <div class="section">
        <div class="receipt-box">
          <div class="receipt-title"><span>${t.settlement}</span></div>
          <div class="receipt-cutout"></div>
          ${(() => {
            // Group expenses by split_among (same participants = same group)
            const groups = [];
            for (const e of expenses) {
              const key = [...e.split_among].sort().join(',');
              const existing = groups.find(g => g.key === key);
              if (existing) {
                existing.descs.push(e.description || t.expense);
                existing.total += e.amount;
              } else {
                groups.push({ key, descs: [e.description || t.expense], total: e.amount, members: e.split_among });
              }
            }
            return groups.map((g, i) => html`
              <div key=${i} style="margin-bottom:${i < groups.length - 1 ? '12px' : '0'};padding-bottom:${i < groups.length - 1 ? '12px;border-bottom:1px dashed #3a5e5e' : '0'}">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px">
                  <span style="font-size:13px;color:#e0d5c4;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">${g.descs.join(' + ')}</span>
                  <span style="font-weight:700;color:#e8a84c;font-size:14px;white-space:nowrap;flex-shrink:0">${currency} ${g.total.toLocaleString()}</span>
                </div>
                <div style="display:flex;gap:8px">
                  ${g.members.map(n => html`<div key=${n} style="display:flex;align-items:center;gap:4px">
                    <${Av} name=${n} size=${22} /><span style="font-size:10px;color:#8aaa9e">${n}</span>
                  </div>`)}
                </div>
              </div>
            `);
          })()}
          <div style="text-align:right;margin-top:10px;padding-top:8px;border-top:1px solid #3a5e5e;font-size:12px;color:#8aaa9e">
            總計 <span style="font-weight:800;color:#e8a84c;font-size:15px;margin-left:4px">${currency} ${result.total.toLocaleString()}</span>
          </div>
        </div>

        ${result.settlements.map((s,idx) => html`
          <div class="result-item" key="${s.from}-${s.to}-${s.amount}-${expenses.length}">
            <div class="left">
              <${Av} name=${s.from} size=${28} />
              <span><span class="result-from">${s.from}</span><span class="result-arrow"> → </span><span class="result-to">${s.to}</span></span>
            </div>
            <span class="result-amount">${currency} ${s.amount.toLocaleString()}</span>
          </div>
        `)}

        <div class="summary-line">
          ${currency} ${result.total.toLocaleString()} ${t.total} · ${nSett} ${nSett>1?t.transfers:t.transfer} ${t.toSettle} <span class="check">✓</span>
        </div>

        ${shareUrl ? html`
          <div class="share-result">
            <div style="margin-bottom:8px">${t.linkCreated}</div>
            <a href=${shareUrl}>${shareUrl}</a>
            <div class="row" style="margin-top:12px">
              <button class="btn" onClick=${copyLink}>${t.copyLink}</button>
              ${navigator.share?html`<button class="btn-outline" onClick=${webShare}>${t.share}</button>`:''}
            </div>
            <div style="margin-top:8px;font-size:12px;color:var(--text-dim)">${t.validFor}</div>
          </div>
        ` : html`
          <${SlideConfirm} label=${t.shareResults} onConfirm=${share} disabled=${sharing} />
        `}
        ${error?html`<div class="error">${error}</div>`:''}
      </div>
    ` : result && nSett===0 && expenses.length>0 ? html`
      <hr class="divider" />
      <div class="summary-line">${t.allSettled} <span class="check">✓</span></div>
    ` : ''}

    <div style="text-align:center;margin-top:24px;font-size:10px;color:var(--text-dim)">
      <a href="/docs" style="color:var(--text-muted)">API Docs</a> · Powered by x402
    </div>
  `;
}

render(html`<${App} />`, document.getElementById('app'));
