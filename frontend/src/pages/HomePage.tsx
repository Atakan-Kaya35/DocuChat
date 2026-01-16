import { useEffect, useState } from 'react';
import { useAuth } from 'react-oidc-context';
import { apiClient, UserInfo } from '../api';

/**
 * HomePage - Main landing page with login/logout and user info display
 */
export default function HomePage() {
  const auth = useAuth();
  const [userInfo, setUserInfo] = useState<UserInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Set up the API client with the token provider
  useEffect(() => {
    apiClient.setTokenProvider(() => auth.user?.access_token ?? null);
  }, [auth.user]);

  // Fetch user info from backend when authenticated
  useEffect(() => {
    if (auth.isAuthenticated && auth.user) {
      setLoading(true);
      setError(null);
      
      apiClient
        .getMe()
        .then((data) => {
          setUserInfo(data);
          setLoading(false);
        })
        .catch((err) => {
          console.error('Failed to fetch user info:', err);
          setError(err.error || 'Failed to fetch user info');
          setLoading(false);
        });
    } else {
      setUserInfo(null);
    }
  }, [auth.isAuthenticated, auth.user]);

  // Show loading state
  if (auth.isLoading) {
    return (
      <div style={styles.container}>
        <div style={styles.card}>
          <h1 style={styles.title}>DocuChat</h1>
          <p>Loading authentication...</p>
        </div>
      </div>
    );
  }

  // Authenticated view
  if (auth.isAuthenticated) {
    return (
      <div style={styles.container}>
        <div style={styles.card}>
          <h1 style={styles.title}>DocuChat</h1>
          
          <div style={styles.userSection}>
            {loading ? (
              <p>Loading user info...</p>
            ) : error ? (
              <div style={styles.error}>
                <p>Error: {error}</p>
                <button style={styles.button} onClick={() => window.location.reload()}>
                  Retry
                </button>
              </div>
            ) : userInfo ? (
              <>
                <div style={styles.avatar}>
                  {userInfo.username.charAt(0).toUpperCase()}
                </div>
                <h2 style={styles.username}>
                  Logged in as <strong>{userInfo.username}</strong>
                </h2>
                {userInfo.email && (
                  <p style={styles.email}>{userInfo.email}</p>
                )}
                <div style={styles.roles}>
                  {userInfo.roles.map((role) => (
                    <span key={role} style={styles.roleTag}>
                      {role}
                    </span>
                  ))}
                </div>
                <p style={styles.userId}>User ID: {userInfo.id}</p>
              </>
            ) : null}
          </div>

          <button
            style={{ ...styles.button, ...styles.logoutButton }}
            onClick={() => auth.signoutRedirect()}
          >
            Logout
          </button>
        </div>
      </div>
    );
  }

  // Not authenticated view
  return (
    <div style={styles.container}>
      <div style={styles.card}>
        <h1 style={styles.title}>DocuChat</h1>
        <p style={styles.subtitle}>Chat with your documents using AI</p>
        
        <button
          style={styles.button}
          onClick={() => {
            console.log('[HomePage] Login button clicked');
            console.log('[HomePage] Starting signin redirect...');
            auth.signinRedirect()
              .then(() => {
                console.log('[HomePage] Redirect initiated (page should redirect now)');
              })
              .catch((err) => {
                console.error('[HomePage] Signin redirect failed:', err);
                alert(`Login failed: ${err.message}\n\nCheck console for details.`);
              });
          }}
        >
          Login with Keycloak
        </button>
        
        <div style={{ marginTop: '1rem', fontSize: '0.75rem', color: '#999' }}>
          <p>Debug: isLoading={String(auth.isLoading)}, isAuth={String(auth.isAuthenticated)}</p>
        </div>
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
    padding: '1rem',
  },
  card: {
    background: 'white',
    borderRadius: '12px',
    padding: '2rem',
    boxShadow: '0 4px 6px rgba(0, 0, 0, 0.1)',
    textAlign: 'center',
    maxWidth: '400px',
    width: '100%',
  },
  title: {
    fontSize: '2rem',
    marginBottom: '0.5rem',
    color: '#1a1a1a',
  },
  subtitle: {
    color: '#666',
    marginBottom: '1.5rem',
  },
  button: {
    background: '#0066cc',
    color: 'white',
    border: 'none',
    padding: '0.75rem 1.5rem',
    fontSize: '1rem',
    borderRadius: '6px',
    cursor: 'pointer',
    transition: 'background 0.2s',
  },
  logoutButton: {
    background: '#dc3545',
    marginTop: '1.5rem',
  },
  userSection: {
    marginTop: '1rem',
  },
  avatar: {
    width: '60px',
    height: '60px',
    borderRadius: '50%',
    background: '#0066cc',
    color: 'white',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '1.5rem',
    fontWeight: 'bold',
    margin: '0 auto 1rem',
  },
  username: {
    fontSize: '1.25rem',
    marginBottom: '0.25rem',
  },
  email: {
    color: '#666',
    marginBottom: '0.5rem',
  },
  roles: {
    display: 'flex',
    gap: '0.5rem',
    justifyContent: 'center',
    marginTop: '0.5rem',
    flexWrap: 'wrap',
  },
  roleTag: {
    background: '#e0e0e0',
    padding: '0.25rem 0.75rem',
    borderRadius: '999px',
    fontSize: '0.875rem',
    color: '#333',
  },
  userId: {
    marginTop: '1rem',
    fontSize: '0.75rem',
    color: '#999',
  },
  error: {
    color: '#dc3545',
    marginBottom: '1rem',
  },
};
