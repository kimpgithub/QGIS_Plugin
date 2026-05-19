import { AuthProvider, useAuth } from './store/AuthContext';
import LoginPage from './pages/LoginPage';
import InspectPage from './pages/InspectPage';

function Routes() {
  const { user } = useAuth();
  return user ? <InspectPage /> : <LoginPage />;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes />
    </AuthProvider>
  );
}
