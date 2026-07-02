// Reproducer for the checkout-svc incident, used by the sandbox counterfactual.
//
// It exercises the exact production path that 500s: checkout() with NO coupon.
//   - On the buggy code, applyDiscount reads `coupon.total` on an undefined coupon
//     and throws "Cannot read properties of undefined" → this script exits non-zero.
//   - Once applyDiscount guards a missing coupon, it returns a valid total → exit 0.
//
// The sandbox runs this before and after applying the agent's proposed patch: a
// FAIL-then-PASS transition is proof the fix resolves the incident.

const assert = require("assert");
const { checkout } = require("./checkout");

const cart = { total: 100, items: [{ price: 50, qty: 2 }] };

// No coupon argument — the request shape that triggers the incident.
const result = checkout(cart);

assert.strictEqual(result.status, "ok", "checkout should succeed without a coupon");
assert.strictEqual(typeof result.total, "number", "total should be a number");
assert.ok(!Number.isNaN(result.total), "total should not be NaN");

console.log("checkout repro passed:", JSON.stringify(result));
