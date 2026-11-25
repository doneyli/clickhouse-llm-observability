const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');
const { chromium } = require('playwright');

test('Team drafts, suggested identities and submitted grants follow the selected project', async t => {
  const browser = await chromium.launch();
  t.after(() => browser.close());
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));

  const maya = 'maya.demo+first@example.invalid';
  const alex = 'alex.demo+first@example.invalid';
  const projectA = {id:'a', name:'Alpha', members:[{email:maya, userId:'m', role:'ADMIN', active:true}]};
  const projectB = {id:'b', name:'Beta', members:[]};
  const state = {applications:[projectA, projectB], demoTag:'first'};
  const workflows = [
    {id:'application', label:'Application', edition:'enterprise'},
    {id:'team', label:'Team', edition:'enterprise', project:true, button:'Onboard team'},
  ];
  const publicDir = path.join(__dirname, '../public');
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    switch (url.pathname) {
      case '/': return route.fulfill({path:path.join(publicDir, 'index.html')});
      case '/app.js': case '/style.css': return route.fulfill({path:path.join(publicDir, url.pathname)});
      case '/api/config': return route.fulfill({json:{workflows}});
      case '/api/state': return route.fulfill({json:state});
      case '/api/script': return route.fulfill({json:{}});
      case '/api/preflight': return route.fulfill({json:{ok:true, organizationProjectCount:2}});
      case '/api/run': return route.fulfill({json:{runId:'fixture'}});
      case '/api/runs/fixture': return route.fulfill({json:{status:'complete', events:[]}});
      default: return route.abort();
    }
  });
  await page.goto('http://portal.test/');
  await page.getByText('Organization connected (2 projects)').waitFor();
  await page.getByRole('button', {name:'Team Enterprise'}).click();
  const email1 = page.locator('#member-one-email'), role1 = page.locator('#member-one-role');
  const email2 = page.locator('#member-two-email'), role2 = page.locator('#member-two-role');
  const project = page.locator('#selected-project');
  const fields = () => Promise.all([email1, role1, email2, role2].map(field => field.inputValue()));
  let revision = 0;
  async function refresh() {
    // Wait for the refreshed inventory to be rendered before inspecting the form.
    projectA.name = 'Alpha ' + (++revision);
    await page.locator('#refresh-state').click();
    await page.getByText(projectA.name, {exact:true}).and(page.locator('strong')).waitFor();
  }

  assert.deepEqual(await fields(), [maya, 'ADMIN', alex, 'VIEWER']);
  await email1.fill('draft@example.invalid');
  await role1.selectOption('VIEWER');
  await refresh();
  assert.deepEqual(await fields(), ['draft@example.invalid', 'VIEWER', alex, 'VIEWER']);

  await project.selectOption('b');
  assert.deepEqual(await fields(), ['', 'MEMBER', alex, 'VIEWER']);
  await role2.selectOption('ADMIN');
  projectA.members.push({email:alex, userId:'x', role:'MEMBER', active:true});
  await refresh();
  assert.deepEqual(await fields(), ['', 'MEMBER', '', 'VIEWER']);
  assert.equal(await page.locator('#parameters').evaluate(form => form.checkValidity()), false);

  await email2.fill('only@example.invalid');
  await role2.selectOption('MEMBER');
  const submission = page.waitForRequest(req => req.method() === 'POST' && req.url().endsWith('/api/run'));
  await page.locator('#run-workflow').click();
  assert.deepEqual((await submission).postDataJSON(), {
    workflow:'team', params:{projectId:'b', members:[{email:'only@example.invalid', role:'MEMBER'}]},
  });
  await page.waitForFunction(() => !document.querySelector('#selected-project').disabled);

  state.demoTag = 'next';
  await refresh();
  assert.deepEqual(await fields(), ['maya.demo+next@example.invalid', 'MEMBER', 'alex.demo+next@example.invalid', 'VIEWER']);
  await project.selectOption('a');
  assert.deepEqual(await fields(), [maya, 'ADMIN', alex, 'MEMBER']);
  projectA.members[0].role = 'VIEWER';
  await refresh();
  assert.equal(await role1.inputValue(), 'VIEWER');
  assert.deepEqual(errors, []);
});
