#!/usr/bin/env node
// Fails the build on any high or critical advisory in the SDK build toolchain, the same
// bar as `npm audit --audit-level=high`, except advisories listed in
// audit-exceptions.json. Each exception names one GHSA id, says why it cannot reach
// anything we ship, and expires; an expired exception fails the build again, so an
// exception can never become a permanent hole. See the servicenow README.

"use strict";

const { execFileSync } = require("node:child_process");
const { readFileSync } = require("node:fs");
const path = require("node:path");

const BLOCKING = new Set(["high", "critical"]);
const GHSA = /GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}/i;

function advisoriesFrom(report) {
    const found = new Map();
    for (const vulnerability of Object.values(report.vulnerabilities || {})) {
        for (const via of vulnerability.via || []) {
            if (typeof via !== "object" || !BLOCKING.has(via.severity)) continue;
            const match = GHSA.exec(via.url || "");
            const id = match ? match[0].toUpperCase() : `npm-${via.source}`;
            if (!found.has(id)) found.set(id, { id, title: via.title, packages: new Set() });
            found.get(id).packages.add(via.name);
        }
    }
    return [...found.values()];
}

function evaluate(report, exceptions, today) {
    const errors = [];
    const notes = [];
    const active = new Map();
    for (const entry of exceptions) {
        if (!entry.id || !entry.reason || !/^\d{4}-\d{2}-\d{2}$/.test(entry.expires || "")) {
            errors.push(`exception ${JSON.stringify(entry)} needs id, reason and expires (YYYY-MM-DD)`);
        } else if (entry.expires < today) {
            errors.push(`exception for ${entry.id} expired on ${entry.expires}; fix it or renew it in review`);
        } else {
            active.set(entry.id.toUpperCase(), entry);
        }
    }
    const advisories = advisoriesFrom(report);
    for (const advisory of advisories) {
        const packages = [...advisory.packages].join(", ");
        if (active.has(advisory.id)) {
            notes.push(`accepted until ${active.get(advisory.id).expires}: ${advisory.id} (${packages})`);
        } else {
            errors.push(`${advisory.id} in ${packages}: ${advisory.title}`);
        }
    }
    const seen = new Set(advisories.map((advisory) => advisory.id));
    for (const id of active.keys()) {
        if (!seen.has(id)) notes.push(`exception for ${id} is no longer needed; remove it`);
    }
    return { errors, notes };
}

function main() {
    const here = __dirname;
    const exceptions = JSON.parse(readFileSync(path.join(here, "audit-exceptions.json"), "utf8"));
    let output;
    try {
        output = execFileSync(
            "npm",
            ["audit", "--package-lock-only", "--json", "--registry=https://registry.npmjs.org/"],
            { cwd: here, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 },
        );
    } catch (error) {
        // npm audit exits non-zero whenever it finds anything; the JSON is still on stdout.
        output = error.stdout;
        if (!output) throw error;
    }
    const report = JSON.parse(output);
    if (report.error) throw new Error(`npm audit failed: ${JSON.stringify(report.error)}`);
    const { errors, notes } = evaluate(report, exceptions, new Date().toISOString().slice(0, 10));
    for (const note of notes) console.log(note);
    for (const error of errors) console.error(`::error::${error}`);
    if (errors.length) process.exit(1);
    console.log("No high or critical advisory outside the dated exceptions.");
}

if (require.main === module) main();

module.exports = { advisoriesFrom, evaluate };
