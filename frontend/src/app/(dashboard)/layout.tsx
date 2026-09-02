import { Sidebar } from "@/components/Sidebar";
import { WorkspaceProvider } from "@/context/WorkspaceContext";

// Route group, not a URL segment: this still serves "/". Split out from the
// root layout so `/login` (outside this group) renders without the Sidebar
// or WorkspaceProvider trying to fetch workspaces while unauthenticated.
export default function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <WorkspaceProvider>
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">{children}</div>
    </WorkspaceProvider>
  );
}
