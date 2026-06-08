import { useState } from 'react';
import { AuthProvider, useAuth } from './store/AuthContext';
import LoginPage from './pages/LoginPage';
import InspectPage from './pages/InspectPage';
import AdminDashboardPage from './pages/AdminDashboardPage';

// 관리 현황 페이지는 발주처 총괄 계정(00000000) 만 접근.
const SUPERADMIN_ID = '00000000';

function Routes() {
  const { user } = useAuth();
  const [showAdmin, setShowAdmin] = useState(false);

  if (!user) return <LoginPage />;

  const isSuperAdmin = user.role === 'master' && user.id === SUPERADMIN_ID;

  // 진입 가드: 슈퍼관리자가 아니면 절대 관리 페이지로 못 들어감.
  if (showAdmin && isSuperAdmin) {
    return <AdminDashboardPage onBack={() => setShowAdmin(false)} />;
  }

  return (
    <InspectPage
      onOpenAdmin={isSuperAdmin ? () => setShowAdmin(true) : undefined}
    />
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Routes />
    </AuthProvider>
  );
}
