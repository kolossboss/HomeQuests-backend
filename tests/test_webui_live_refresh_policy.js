const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadPolicy() {
  const appPath = path.join(__dirname, "..", "app", "web", "static", "app.js");
  const source = fs.readFileSync(appPath, "utf8");
  const startMarker = "// BEGIN LIVE REFRESH POLICY";
  const endMarker = "// END LIVE REFRESH POLICY";
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.notEqual(start, -1, "Live-Refresh-Policy-Block fehlt");
  assert.notEqual(end, -1, "Live-Refresh-Policy-Block ist nicht abgeschlossen");

  const context = {};
  vm.runInNewContext(
    `${source.slice(start, end + endMarker.length)}
      this.__policy = {
        createLiveRefreshScope,
        mergeLiveRefreshScopes,
        buildLiveRefreshDomainPlan,
        liveRefreshScopeForEvent,
      };`,
    context,
    { filename: appPath }
  );
  return context.__policy;
}

const policy = loadPolicy();

function scopeDomains(eventType) {
  const scope = policy.liveRefreshScopeForEvent(eventType);
  return { full: scope.full, domains: [...scope.domains] };
}

function requestCount(scope, role) {
  const requestCosts = {
    members: 1,
    tasks: 1,
    specialTasks: 1,
    events: 1,
    rewards: 1,
    redemptions: 1,
    points: role === "child" ? 3 : 2,
    achievements: 1,
    notificationChannels: 1,
    haSettings: 1,
    haUsers: 1,
    systemRuntime: 1,
    systemEvents: 1,
    dbTools: 1,
  };
  return policy
    .buildLiveRefreshDomainPlan(scope, role)
    .reduce((total, domain) => total + requestCosts[domain], 0);
}

test("bekannte Serverevents werden auf abhängige Domänen begrenzt", () => {
  assert.deepEqual(scopeDomains("task.created"), { full: false, domains: ["tasks"] });
  assert.deepEqual(scopeDomains("task.reviewed"), {
    full: false,
    domains: ["tasks", "points", "achievements"],
  });
  assert.deepEqual(scopeDomains("reward.redeem_requested"), {
    full: false,
    domains: ["redemptions", "points"],
  });
  assert.deepEqual(scopeDomains("achievement.reward_claimed"), {
    full: false,
    domains: ["achievements", "points"],
  });
  assert.deepEqual(scopeDomains("notification.test"), {
    full: false,
    domains: ["systemEvents"],
  });
  assert.deepEqual(scopeDomains("system.db.backup_created"), {
    full: false,
    domains: ["systemEvents", "dbTools"],
  });
});

test("Mitgliedschaft, Recovery und unbekannte Events bleiben Vollrefresh", () => {
  for (const eventType of [
    "member.updated",
    "membership.changed",
    "role.changed",
    "connected",
    "reconnected",
    "totally.new.event",
    "task.future_variant",
  ]) {
    assert.equal(scopeDomains(eventType).full, true, eventType);
  }
});

test("Debounce-Scopes vereinigen sich; Vollrefresh dominiert", () => {
  const tasks = policy.liveRefreshScopeForEvent("task.created");
  const points = policy.liveRefreshScopeForEvent("points.adjusted");
  const merged = policy.mergeLiveRefreshScopes(tasks, points);
  assert.deepEqual([...merged.domains].sort(), ["achievements", "points", "tasks"]);
  assert.equal(merged.full, false);

  const full = policy.liveRefreshScopeForEvent("unknown.event");
  const dominated = policy.mergeLiveRefreshScopes(merged, full);
  assert.equal(dominated.full, true);
});

test("Domain-Plan berücksichtigt Rollen und misst den Fanout", () => {
  const fullChild = requestCount(policy.liveRefreshScopeForEvent("unknown"), "child");
  const fullManager = requestCount(policy.liveRefreshScopeForEvent("unknown"), "parent");
  assert.equal(fullChild, 10);
  assert.equal(fullManager, 15);

  assert.equal(requestCount(policy.liveRefreshScopeForEvent("task.created"), "child"), 1);
  assert.equal(requestCount(policy.liveRefreshScopeForEvent("task.created"), "parent"), 1);
  assert.equal(requestCount(policy.liveRefreshScopeForEvent("reward.redeem_requested"), "child"), 4);
  assert.equal(requestCount(policy.liveRefreshScopeForEvent("system.db.backup_created"), "parent"), 2);
});
