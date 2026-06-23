// checkout.js — order checkout logic for the demo "checkout-svc".
// Contains a deliberate bug the DevOps Copilot is meant to find.

function calcSubtotal(cart) {
  return cart.items.reduce((sum, item) => sum + item.price * item.qty, 0);
}

function applyDiscount(cart, coupon) {
  const subtotal = cart.total;
  // BUG: `coupon` is undefined when the request has no coupon, so reading
  // `coupon.total` throws: "Cannot read properties of undefined (reading 'total')".
  const pct = coupon.total;
  return subtotal - subtotal * pct;
}

function checkout(cart, coupon) {
  const total = applyDiscount(cart, coupon);
  return { status: "ok", total };
}

module.exports = { calcSubtotal, applyDiscount, checkout };
