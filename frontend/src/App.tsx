import { useEffect } from 'react';
import { Routes, Route } from 'react-router-dom';
import { useAuth } from 'react-oidc-context';
import HomePage from './pages/HomePage';
import CallbackPage from './pages/CallbackPage';
import ChatPage from './pages/ChatPage';

function App() {
  const auth = useAuth();

  // Log auth state changes for debugging
  useEffect(() => {
    console.log('[App] Auth state changed:', {
      isLoading: auth.isLoading,
      isAuthenticated: auth.isAuthenticated,
      user: auth.user ? {
        profile: auth.user.profile,
        expires_at: auth.user.expires_at,
      } : null,
      error: auth.error?.message,
      activeNavigator: auth.activeNavigator,
    });
  }, [auth.isLoading, auth.isAuthenticated, auth.user, auth.error, auth.activeNavigator]);

  // Show loading state while processing auth
  if (auth.isLoading) {
    console.log('[App] Auth is loading...');
    return (
      <div style={{ padding: '2rem', textAlign: 'center' }}>
        <h1>DocuChat</h1>
        <p>Loading authentication...</p>
        <p style={{ fontSize: '0.8rem', color: '#666' }}>
          Active navigator: {auth.activeNavigator || 'none'}
        </p>
      </div>
    );
  }

  // Handle OIDC errors
  if (auth.error) {
    console.error('[App] Auth error:', auth.error);
    return (
      <div style={{ padding: '2rem', textAlign: 'center' }}>
        <h1>Authentication Error</h1>
        <p style={{ color: 'red' }}>{auth.error.message}</p>
        <pre style={{ 
          textAlign: 'left', 
          background: '#f5f5f5', 
          padding: '1rem',
          maxWidth: '600px',
          margin: '1rem auto',
          overflow: 'auto',
          fontSize: '0.8rem'
        }}>
          {JSON.stringify(auth.error, null, 2)}
        </pre>
        <button 
          onClick={() => {
            console.log('[App] Retrying signin redirect...');
            auth.signinRedirect();
          }}
          style={{ padding: '0.5rem 1rem', cursor: 'pointer' }}
        >
          Try Again
        </button>
        <button 
          onClick={() => {
            console.log('[App] Clearing stale state...');
            auth.removeUser();
            window.location.href = '/';
          }}
          style={{ padding: '0.5rem 1rem', cursor: 'pointer', marginLeft: '0.5rem' }}
        >
          Clear & Restart
        </button>
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/callback" element={<CallbackPage />} />
      <Route path="/chat" element={<ChatPage />} />
    </Routes>
  );
}

export default App;
