import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "@/app/login/page";

const { signInWithPassword, signUp, resend, push, refresh } = vi.hoisted(() => ({
  signInWithPassword: vi.fn(),
  signUp: vi.fn(),
  resend: vi.fn(),
  push: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh }) }));
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { signInWithPassword, signUp, resend } }),
}));

/**
 * The mode toggle and the submit control share an accessible name ("Log In"
 * appears on both the tab and the button), so role+name alone is ambiguous.
 * Selecting by `type` is what actually distinguishes them.
 */
function button(name: RegExp, type: "submit" | "button") {
  return screen
    .getAllByRole("button", { name })
    .find((element) => (element as HTMLButtonElement).type === type) as HTMLButtonElement;
}

async function submitLogin(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/email/i), "alex@example.com");
  await user.type(screen.getByLabelText(/password/i), "hunter2hunter2");
  await user.click(button(/^log in$/i, "submit"));
}

describe("LoginPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    signInWithPassword.mockResolvedValue({ error: null });
    signUp.mockResolvedValue({ data: { session: null }, error: null });
    resend.mockResolvedValue({ error: null });
  });

  it("signs in and redirects on success", async () => {
    const user = userEvent.setup();
    render(<LoginPage />);

    await submitLogin(user);

    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
    expect(signInWithPassword).toHaveBeenCalledWith({
      email: "alex@example.com",
      password: "hunter2hunter2",
    });
  });

  it("surfaces a sign-in error without redirecting", async () => {
    const user = userEvent.setup();
    signInWithPassword.mockResolvedValue({ error: { message: "Invalid login credentials" } });
    render(<LoginPage />);

    await submitLogin(user);

    expect(await screen.findByText("Invalid login credentials")).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("offers a resend when sign-in fails because the email is unconfirmed", async () => {
    const user = userEvent.setup();
    signInWithPassword.mockResolvedValue({
      error: { message: "Email not confirmed", code: "email_not_confirmed" },
    });
    render(<LoginPage />);

    await submitLogin(user);

    // Without this the user has no route forward from this screen at all:
    // their original link is gone and nothing offers another.
    const resendButton = await screen.findByRole("button", { name: /resend confirmation email/i });

    await user.click(resendButton);

    await waitFor(() =>
      expect(resend).toHaveBeenCalledWith(
        expect.objectContaining({ type: "signup", email: "alex@example.com" })
      )
    );
  });

  it("does not offer a resend for ordinary credential failures", async () => {
    const user = userEvent.setup();
    signInWithPassword.mockResolvedValue({ error: { message: "Invalid login credentials" } });
    render(<LoginPage />);

    await submitLogin(user);

    await screen.findByText("Invalid login credentials");
    expect(
      screen.queryByRole("button", { name: /resend confirmation email/i })
    ).not.toBeInTheDocument();
  });

  it("holds the resend button closed on a cooldown after a send", async () => {
    const user = userEvent.setup();
    signInWithPassword.mockResolvedValue({
      error: { message: "Email not confirmed", code: "email_not_confirmed" },
    });
    render(<LoginPage />);
    await submitLogin(user);

    await user.click(await screen.findByRole("button", { name: /resend confirmation email/i }));

    // Supabase rate-limits resends with an unhelpful 429, so the UI stops the
    // user reaching it rather than letting them hit a confusing error.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /resend confirmation email \(\d+s\)/i })).toBeDisabled()
    );
  });

  it("confirms a resend without revealing whether the address is registered", async () => {
    const user = userEvent.setup();
    signInWithPassword.mockResolvedValue({
      error: { message: "Email not confirmed", code: "email_not_confirmed" },
    });
    render(<LoginPage />);
    await submitLogin(user);

    await user.click(await screen.findByRole("button", { name: /resend confirmation email/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/if that address needs confirming/i);
  });

  it("tells a new signup to confirm their email and offers a resend", async () => {
    const user = userEvent.setup();
    render(<LoginPage />);

    await user.click(button(/^sign up$/i, "button"));
    await user.type(screen.getByLabelText(/email/i), "new@example.com");
    await user.type(screen.getByLabelText(/password/i), "hunter2hunter2");
    await user.click(button(/^sign up$/i, "submit"));

    expect(await screen.findByText(/check your email to confirm/i)).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: /resend confirmation email/i })
    ).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("signs a new user straight in when confirmation is disabled", async () => {
    const user = userEvent.setup();
    signUp.mockResolvedValue({ data: { session: { access_token: "jwt" } }, error: null });
    render(<LoginPage />);

    await user.click(button(/^sign up$/i, "button"));
    await user.type(screen.getByLabelText(/email/i), "new@example.com");
    await user.type(screen.getByLabelText(/password/i), "hunter2hunter2");
    await user.click(button(/^sign up$/i, "submit"));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
  });

  it("reports a network failure instead of hanging", async () => {
    const user = userEvent.setup();
    signInWithPassword.mockRejectedValue(new Error("network down"));
    render(<LoginPage />);

    await submitLogin(user);

    expect(await screen.findByText(/unable to reach supabase/i)).toBeInTheDocument();
    // The form must become usable again rather than staying stuck on "wait".
    await waitFor(() => expect(button(/^log in$/i, "submit")).toBeEnabled());
  });
});
