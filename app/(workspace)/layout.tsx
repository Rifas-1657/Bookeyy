import { RequireAuth } from '@/components/auth/require-auth'
import { WorkspaceShell } from '@/components/workspace/workspace-shell'

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <WorkspaceShell>{children}</WorkspaceShell>
    </RequireAuth>
  )
}
