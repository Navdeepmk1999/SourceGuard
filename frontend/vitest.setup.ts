// Registers jest-dom's matchers (toBeInTheDocument, toHaveFocus, ...) with
// Vitest's expect, and augments its types.
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// RTL does not auto-clean when globals are disabled, and a leaked component
// tree makes the next test's queries ambiguous rather than failing outright.
afterEach(() => {
  cleanup();
});

// jsdom implements no layout, so Element.prototype.scrollTo does not exist and
// any component that auto-scrolls (ChatPanel follows streamed tokens) throws
// on render. Stubbed globally rather than per-test: the call is a no-op for
// assertions either way, and its absence is a jsdom gap, not a behaviour
// worth reproducing.
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = function scrollTo() {};
}
