/**
 * E2E tests for Split Senpai (分帳仙貝)
 * Run: node tests/test_e2e.mjs
 * Requires: npm install playwright (in /tmp or globally)
 */
import { chromium } from 'playwright';

const URL = process.env.TEST_URL || 'https://sfd9k548wj.execute-api.ap-northeast-1.amazonaws.com';
let passed = 0, failed = 0;

async function test(name, fn) {
  try {
    await fn();
    passed++;
    console.log(`  ✅ ${name}`);
  } catch (e) {
    failed++;
    console.log(`  ❌ ${name}: ${e.message}`);
  }
}

function assert(condition, msg) { if (!condition) throw new Error(msg || 'assertion failed'); }

console.log(`\n🧪 E2E Tests — Split Senpai`);
console.log(`   Target: ${URL}\n`);

const browser = await chromium.launch({ headless: true });

// ── Test: Page loads with app ──
await test('Homepage loads with Preact app', async () => {
  const page = await browser.newPage();
  await page.goto(URL);
  await page.waitForTimeout(4000);
  const count = await page.evaluate(() => document.getElementById('app').childElementCount);
  assert(count > 0, `app has ${count} children`);
  const title = await page.textContent('h1');
  assert(title.includes('Senpai') || title.includes('仙貝') || title.includes('先輩'), `title is ${title}`);
  await page.close();
});

// ── Test: Language toggle ──
await test('Language toggle cycles EN → 中 → JA', async () => {
  const page = await browser.newPage();
  await page.goto(URL);
  await page.waitForTimeout(4000);
  // Find and click lang button
  const btn = await page.$('.lang-btn');
  assert(btn, 'lang button exists');
  const t1 = await btn.textContent();
  await btn.click(); await page.waitForTimeout(300);
  const t2 = await page.$eval('.lang-btn', el => el.textContent);
  await btn.click(); await page.waitForTimeout(300);
  const t3 = await page.$eval('.lang-btn', el => el.textContent);
  // Should cycle through 3 different labels
  assert(t1 !== t2 || t2 !== t3, `labels: ${t1} → ${t2} → ${t3}`);
  await page.close();
});

// ── Test: Add participants ──
await test('Add 2 participants via Enter key', async () => {
  const page = await browser.newPage();
  await page.goto(URL);
  await page.waitForTimeout(4000);
  await page.fill('input[placeholder]', 'Alice');
  await page.keyboard.press('Enter');
  await page.waitForTimeout(500);
  await page.fill('input[placeholder]', 'Bob');
  await page.keyboard.press('Enter');
  await page.waitForTimeout(500);
  const chips = await page.$$('.chip');
  assert(chips.length === 2, `expected 2 chips, got ${chips.length}`);
  await page.close();
});

// ── Test: Add expense with calculator ──
await test('Add expense using calculator keypad', async () => {
  const page = await browser.newPage();
  await page.goto(URL);
  await page.waitForTimeout(4000);
  // Add 2 people
  await page.fill('input[placeholder]', 'Alice');
  await page.keyboard.press('Enter'); await page.waitForTimeout(400);
  await page.fill('input[placeholder]', 'Bob');
  await page.keyboard.press('Enter'); await page.waitForTimeout(400);
  // Click add expense
  const addBtn = await page.$('.btn-add-hint');
  assert(addBtn, 'add expense button exists with hint class');
  await addBtn.click(); await page.waitForTimeout(400);
  // Use calculator keys
  const keys = await page.$$('.calc-key');
  assert(keys.length === 16, `expected 16 calc keys, got ${keys.length}`);
  // Type 1200 via calculator: 1, 2, 0, 0
  for (const k of ['1', '2', '0', '0']) {
    const key = await page.$(`button.calc-key:has-text("${k}")`);
    if (key) await key.click();
    await page.waitForTimeout(100);
  }
  // Check amount input shows 1200
  const val = await page.$eval('#amt-input', el => el.value);
  assert(val === '1200', `expected 1200, got ${val}`);
  // Click Add button
  const confirmBtn = await page.$('button.btn:has-text("Add")');
  if (!confirmBtn) {
    const confirmBtnZh = await page.$('button.btn:has-text("新增")');
    if (confirmBtnZh) await confirmBtnZh.click();
  } else {
    await confirmBtn.click();
  }
  await page.waitForTimeout(500);
  // Should have expense card
  const cards = await page.$$('.expense-card');
  assert(cards.length >= 1, `expected expense card, got ${cards.length}`);
  await page.close();
});

// ── Test: Settlement appears after expense ──
await test('Settlement section appears with result cards', async () => {
  const page = await browser.newPage();
  await page.goto(URL);
  await page.waitForTimeout(4000);
  // Add people + expense
  await page.fill('input[placeholder]', 'Alice');
  await page.keyboard.press('Enter'); await page.waitForTimeout(400);
  await page.fill('input[placeholder]', 'Bob');
  await page.keyboard.press('Enter'); await page.waitForTimeout(400);
  // Add expense
  const addBtn = await page.$('text=+ Add Expense');
  if (!addBtn) { const zh = await page.$('text=+ 新增帳單'); if (zh) await zh.click(); }
  else await addBtn.click();
  await page.waitForTimeout(400);
  // Type amount via calculator
  for (const k of ['5','0','0']) {
    const key = await page.$(`.calc-key:has-text("${k}")`);
    if (key) await key.click();
    await page.waitForTimeout(50);
  }
  // Find and click Add/新增 button
  const addConfirm = await page.$('button.btn:not(.btn-outline)');
  if (addConfirm) await addConfirm.click();
  await page.waitForTimeout(500);
  // Check for result items
  const results = await page.$$('.result-item');
  assert(results.length >= 1, `expected result items, got ${results.length}`);
  // Check for receipt box
  const receipt = await page.$('.receipt-box');
  assert(receipt, 'receipt box exists');
  await page.close();
});

// ── Test: Docs page loads ──
await test('GET /docs returns Swagger UI', async () => {
  const page = await browser.newPage();
  await page.goto(`${URL}/docs`);
  await page.waitForTimeout(3000);
  const title = await page.title();
  assert(title.includes('Senpai') || title.includes('Split'), `title: ${title}`);
  await page.close();
});

// ── Test: Health endpoint ──
await test('GET /health returns ok', async () => {
  const page = await browser.newPage();
  const response = await page.goto(`${URL}/health`);
  const body = await response.json();
  assert(body.status === 'ok', `status: ${body.status}`);
  await page.close();
});

// ── Test: 404 page ──
await test('GET /s/nonexistent returns styled 404', async () => {
  const page = await browser.newPage();
  const response = await page.goto(`${URL}/s/this-does-not-exist-12345`);
  assert(response.status() === 404, `status: ${response.status()}`);
  const body = await page.textContent('body');
  assert(body.includes('not found') || body.includes('Not found'), `body: ${body.substring(0,100)}`);
  await page.close();
});

// ── Test: Share endpoint (navigate to page first so fetch has same origin) ──
await test('POST /v1/share returns share_id', async () => {
  const page = await browser.newPage();
  await page.goto(URL);
  await page.waitForTimeout(2000);
  const resp = await page.evaluate(async () => {
    const r = await fetch('/v1/share', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({currency:'TWD',participants:['A','B'],
        expenses:[{paid_by:'A',amount:100,split_among:['A','B']}]})
    });
    return { status: r.status, body: await r.json() };
  });
  assert(resp.status === 200, `status: ${resp.status}`);
  assert(resp.body.share_id, 'has share_id');
  assert(resp.body.url.startsWith('/s/'), `url: ${resp.body.url}`);
  await page.close();
});

await browser.close();

console.log(`\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━`);
console.log(`  Results: ${passed} passed, ${failed} failed`);
console.log(`━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n`);

process.exit(failed > 0 ? 1 : 0);
