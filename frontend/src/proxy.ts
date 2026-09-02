import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Protects the dashboard (`/`) behind Supabase auth. Named `proxy` (not
 * `middleware`) and filed as `src/proxy.ts`: Next.js 16 renamed the file
 * convention (the old `middleware.ts`/`export function middleware` still
 * works as a deprecated compatibility shim, but this pinned version's own
 * docs - see frontend/AGENTS.md - call out `proxy` as current).
 *
 * Follows the official `@supabase/ssr` contract: `getAll`/`setAll` cookie
 * methods (not the deprecated single-cookie `get`/`set`/`remove`), and
 * `supabase.auth.getUser()` rather than reading a cookie-decoded session
 * directly - `getUser()` revalidates the token against Supabase, so an
 * expired/tampered cookie can't pass as authenticated.
 */
export async function proxy(request: NextRequest) {
  let supabaseResponse = NextResponse.next({ request });

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

  // Misconfigured - fail open rather than lock the app behind a broken
  // redirect loop the operator can't see. `getCurrentUser`'s backend
  // counterpart still fails closed (500) on missing config; this is a
  // frontend routing guard, not the actual security boundary.
  if (!url || !publishableKey) {
    return supabaseResponse;
  }

  const supabase = createServerClient(url, publishableKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
        supabaseResponse = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) =>
          supabaseResponse.cookies.set(name, value, options)
        );
      },
    },
  });

  const {
    data: { user },
  } = await supabase.auth.getUser();

  const isLoginPage = request.nextUrl.pathname.startsWith("/login");

  if (!user && !isLoginPage) {
    const loginUrl = request.nextUrl.clone();
    loginUrl.pathname = "/login";
    return NextResponse.redirect(loginUrl);
  }

  if (user && isLoginPage) {
    const dashboardUrl = request.nextUrl.clone();
    dashboardUrl.pathname = "/";
    return NextResponse.redirect(dashboardUrl);
  }

  return supabaseResponse;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
