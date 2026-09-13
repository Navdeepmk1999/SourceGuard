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
