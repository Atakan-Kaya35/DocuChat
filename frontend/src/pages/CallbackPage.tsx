import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from 'react-oidc-context';

/**
 * CallbackPage - Handles the OIDC redirect callback
 * 
 * After Keycloak authenticates the user, it redirects back here with
 * an authorization code. The oidc-client-ts library handles the
 * code exchange automatically.
 */
export default function CallbackPage() {
  const auth = useAuth();
  const navigate = useNavigate();

  // Log callback state for debugging
  useEffect(() => {
    console.log('[Callback] Page loaded');
    console.log('[Callback] URL:', window.location.href);
    console.log('[Callback] Search params:', window.location.search);
    console.log('[Callback] Auth state:', {
      isLoading: auth.isLoading,
      isAuthenticated: auth.isAuthenticated,
      error: auth.error?.message,
      activeNavigator: auth.activeNavigator,
    });
  }, [auth.isLoading, auth.isAuthenticated, auth.error, auth.activeNavigator]);

  useEffect(() => {
    // Once authentication is complete, redirect to home
    if (!auth.isLoading && auth.isAuthenticated) {
      console.log('[Callback] Auth complete, navigating to home');
      // Clean up the URL (remove code, state params)
      navigate('/', { replace: true });
    }
  }, [auth.isLoading, auth.isAuthenticated, navigate]);

  // Show error if authentication failed
  if (auth.error) {
    return (
      <div style={styles.container}>
        <div style={styles.card}>
          <h2>Authentication Error</h2>
          <p style={styles.error}>{auth.error.message}</p>
          <button
            style={styles.button}
            onClick={() => navigate('/', { replace: true })}
          >
            Return Home
          </button>
        </div>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <div style={styles.card}>
        <h2>Completing login...</h2>
        <div style={styles.spinner}></div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  card: {
    background: 'white',
    borderRadius: '12px',
    padding: '2rem',
    boxShadow: '0 4px 6px rgba(0, 0, 0, 0.1)',
    textAlign: 'center',
  },
  spinner: {
    width: '40px',
    height: '40px',
    border: '4px solid #f3f3f3',
    borderTop: '4px solid #0066cc',
    borderRadius: '50%',
    margin: '1rem auto',
    animation: 'spin 1s linear infinite',
  },
  error: {
    color: '#dc3545',
    marginBottom: '1rem',
  },
  button: {
    background: '#0066cc',
    color: 'white',
    border: 'none',
    padding: '0.75rem 1.5rem',
    fontSize: '1rem',
    borderRadius: '6px',
    cursor: 'pointer',
  },
};
