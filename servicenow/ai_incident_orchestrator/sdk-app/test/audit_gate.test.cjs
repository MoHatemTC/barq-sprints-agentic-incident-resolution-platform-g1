"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { evaluate } = require("../audit-gate.cjs");

const BRACES = "GHSA-vfj7-8cjw-p6xm";

function report(...advisories) {
    const vulnerabilities = {};
    for (const [name, severity, id] of advisories) {
        vulnerabilities[name] = {
            via: [{ name, severity, title: `${name} issue`, url: `https://github.com/advisories/${id}` }],
        };
    }
    // A transitive entry only names the package it depends on; it must not count twice.
    vulnerabilities["micromatch"] = { via: ["braces"] };
    return { vulnerabilities };
}

const exception = { id: BRACES, reason: "build-time only", expires: "2026-10-31" };

test("an excepted advisory before its expiry passes", () => {
    const result = evaluate(report(["braces", "high", BRACES]), [exception], "2026-10-03");
    assert.deepEqual(result.errors, []);
    assert.match(result.notes[0], /accepted until 2026-10-31/);
});

test("any other high advisory still fails", () => {
    const result = evaluate(
        report(["braces", "high", BRACES], ["undici", "high", "GHSA-aaaa-bbbb-cccc"]),
        [exception],
        "2026-10-03",
    );
    assert.equal(result.errors.length, 1);
    assert.match(result.errors[0], /GHSA-AAAA-BBBB-CCCC in undici/);
});

test("critical counts as blocking and moderate does not", () => {
    const result = evaluate(
        report(["a", "critical", "GHSA-1111-2222-3333"], ["b", "moderate", "GHSA-4444-5555-6666"]),
        [],
        "2026-10-03",
    );
    assert.equal(result.errors.length, 1);
    assert.match(result.errors[0], /GHSA-1111-2222-3333/);
});

test("an expired exception fails the build again", () => {
    const result = evaluate(report(["braces", "high", BRACES]), [exception], "2026-11-01");
    assert.ok(result.errors.some((error) => /expired on 2026-10-31/.test(error)));
    assert.ok(result.errors.some((error) => error.startsWith(BRACES.toUpperCase())));
});

test("an exception without a reason or expiry is rejected", () => {
    const result = evaluate(report(), [{ id: BRACES }], "2026-10-03");
    assert.match(result.errors[0], /needs id, reason and expires/);
});

test("an exception that is no longer needed is reported", () => {
    const result = evaluate(report(), [exception], "2026-10-03");
    assert.deepEqual(result.errors, []);
    assert.match(result.notes[0], /no longer needed/);
});
