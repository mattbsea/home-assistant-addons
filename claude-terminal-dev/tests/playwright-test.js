// Playwright test for Claude Terminal Dev web UI
// Tests direct connection (bypassing HA ingress)
const { chromium, webkit } = require('playwright');

const TARGET_URL = process.env.TARGET_URL || 'http://172.30.33.19:7682';
const BROWSERS = (process.env.BROWSERS || 'chromium').split(',');

async function testBrowser(browserType, name) {
    console.log('\n=== Testing ' + name + ' ===');
    const launchOpts = name === 'chromium' ? { args: ['--no-sandbox'] } : {};
    const browser = await browserType.launch(launchOpts);
    const page = await browser.newPage();
    const results = { pass: 0, fail: 0, errors: [] };

    function assert(condition, message) {
        if (condition) {
            console.log('  PASS: ' + message);
            results.pass++;
        } else {
            console.log('  FAIL: ' + message);
            results.fail++;
            results.errors.push(message);
        }
    }

    try {
        // Test 1: Page loads
        console.log('  Loading ' + TARGET_URL + '...');
        const response = await page.goto(TARGET_URL, { timeout: 15000 });
        assert(response.ok(), 'Page loads with status ' + response.status());

        // Test 2: Title
        const title = await page.title();
        assert(title === 'Claude Terminal', 'Page title is "' + title + '"');

        // Test 3: Tab bar exists
        const tabBar = await page.$('#tab-bar');
        assert(tabBar !== null, 'Tab bar element exists');

        // Test 4: New tab button exists
        const newTabBtn = await page.$('#new-tab-btn');
        assert(newTabBtn !== null, 'New tab button exists');

        // Test 5: Terminal container exists
        const termContainer = await page.$('#terminal-container');
        assert(termContainer !== null, 'Terminal container exists');

        // Test 6: Wait for WebSocket connection and tab creation
        await page.waitForTimeout(3000);

        // Test 7: Check for tabs (server should auto-create Claude tab)
        const tabs = await page.$$('.tab');
        assert(tabs.length > 0, 'Found ' + tabs.length + ' tab(s) in tab bar');

        // Test 8: Active tab exists
        const activeTab = await page.$('.tab.active');
        assert(activeTab !== null, 'An active tab exists');

        // Test 9: Check tab label
        if (activeTab) {
            const label = await activeTab.$('.tab-label');
            const labelText = await label.textContent();
            assert(labelText.length > 0, 'Active tab label: "' + labelText + '"');
        }

        // Test 10: Terminal wrapper is visible
        const activeWrapper = await page.$('.terminal-wrapper.active');
        assert(activeWrapper !== null, 'Active terminal wrapper exists');

        // Test 11: xterm.js terminal rendered
        const xtermScreen = await page.$('.xterm-screen');
        assert(xtermScreen !== null, 'xterm.js screen element rendered');

        // Test 12: Terminal has content (xterm rows)
        const xtermRows = await page.$$('.xterm-rows > div');
        assert(xtermRows.length > 0, 'Terminal has ' + xtermRows.length + ' row(s)');

        // Test 13: Check connection status is NOT visible (means connected)
        const connStatus = await page.$('#connection-status');
        const connClasses = await connStatus.getAttribute('class');
        const connVisible = connClasses && connClasses.includes('visible');
        assert(!connVisible, 'Connection status banner is hidden (WebSocket connected)');

        // Test 14: New tab menu works
        await newTabBtn.click();
        await page.waitForTimeout(500);
        const menu = await page.$('#new-tab-menu.open');
        assert(menu !== null, 'New tab menu opens on click');

        // Test 15: Menu has expected items
        const menuItems = await page.$$('#new-tab-menu .menu-item');
        assert(menuItems.length === 4, 'Menu has ' + menuItems.length + ' items (expected 4)');

        // Test 16: Create a Shell tab
        const shellItem = await page.$('.menu-item[data-action="shell"]');
        if (shellItem) {
            await shellItem.click();
            await page.waitForTimeout(2000);
            const tabsAfter = await page.$$('.tab');
            assert(tabsAfter.length > tabs.length, 'New shell tab created (now ' + tabsAfter.length + ' tabs)');
        }

        // Test 17: Close tab button works
        const tabsBeforeClose = await page.$$('.tab');
        const closeBtn = await page.$('.tab.active .tab-close');
        if (closeBtn && tabsBeforeClose.length > 1) {
            await closeBtn.click();
            await page.waitForTimeout(1000);
            const tabsAfterClose = await page.$$('.tab');
            assert(tabsAfterClose.length < tabsBeforeClose.length, 'Tab closed (now ' + tabsAfterClose.length + ' tabs)');
        }

        // Take screenshot
        await page.screenshot({ path: '/results/' + name + '-final.png', fullPage: true });
        console.log('  Screenshot saved: /results/' + name + '-final.png');

    } catch (e) {
        console.log('  ERROR: ' + e.message);
        results.errors.push(e.message);
        results.fail++;
        try {
            await page.screenshot({ path: '/results/' + name + '-error.png', fullPage: true });
        } catch (e2) {}
    } finally {
        await browser.close();
    }

    console.log('\n  ' + name + ' Results: ' + results.pass + ' passed, ' + results.fail + ' failed');
    if (results.errors.length > 0) {
        console.log('  Errors: ' + results.errors.join('; '));
    }
    return results;
}

async function main() {
    console.log('Target URL: ' + TARGET_URL);
    console.log('Browsers: ' + BROWSERS.join(', '));

    const allResults = {};

    for (const name of BROWSERS) {
        const browserType = name === 'webkit' ? webkit : chromium;
        allResults[name] = await testBrowser(browserType, name);
    }

    console.log('\n=== SUMMARY ===');
    var totalPass = 0, totalFail = 0;
    for (const [name, r] of Object.entries(allResults)) {
        console.log(name + ': ' + r.pass + ' passed, ' + r.fail + ' failed');
        totalPass += r.pass;
        totalFail += r.fail;
    }
    console.log('Total: ' + totalPass + ' passed, ' + totalFail + ' failed');

    // Write results JSON
    const fs = require('fs');
    fs.writeFileSync('/results/results.json', JSON.stringify(allResults, null, 2));

    process.exit(totalFail > 0 ? 1 : 0);
}

main().catch(function(e) {
    console.error('Fatal error:', e);
    process.exit(1);
});
