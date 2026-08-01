import { FileSearch, MessageSquare, Search, ShieldCheck } from "lucide-react";

export default function Home() {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-zinc-800 px-6">
        <div>
          <h1 className="text-sm font-semibold text-zinc-100">Dashboard</h1>
          <p className="text-xs text-zinc-500">No workspace selected</p>
        </div>
        <button
          type="button"
          className="rounded-md bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-indigo-400"
        >
          Upload Document
        </button>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px]">
        <section className="flex min-h-0 flex-col border-r border-zinc-800">
          <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-zinc-900 ring-1 ring-zinc-800">
              <MessageSquare className="h-5 w-5 text-zinc-500" />
            </div>
            <div>
              <p className="text-sm font-medium text-zinc-300">
                Ask a question about your documents
              </p>
              <p className="mt-1 max-w-sm text-sm text-zinc-500">
                Answers are grounded in your workspace, and every claim is
                verified against its source before it&apos;s shown to you.
              </p>
            </div>
          </div>

          <div className="border-t border-zinc-800 p-4">
            <div className="flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2">
              <Search className="h-4 w-4 shrink-0 text-zinc-500" />
              <input
                type="text"
                disabled
                placeholder="Select a workspace to start querying..."
                className="w-full bg-transparent text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none disabled:cursor-not-allowed"
              />
            </div>
          </div>
        </section>

        <aside className="flex min-h-0 flex-col">
          <div className="flex h-12 shrink-0 items-center gap-2 border-b border-zinc-800 px-4">
            <ShieldCheck className="h-4 w-4 text-indigo-400" />
            <h2 className="text-sm font-semibold text-zinc-100">
              Verification Audit Log
            </h2>
          </div>
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
            <FileSearch className="h-6 w-6 text-zinc-700" />
            <p className="text-sm text-zinc-500">
              Claim-level verification results will appear here in real time
              as answers are generated.
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}
