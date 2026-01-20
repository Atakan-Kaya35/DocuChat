import { useEffect } from 'react';
import { Routes, Route } from 'react-router-dom';
import { useAuth } from 'react-oidc-context';
import { Spin, Alert, Button, Typography, ConfigProvider, Space, theme } from 'antd';
import { ReloadOutlined, ClearOutlined } from '@ant-design/icons';
import HomePage from './pages/HomePage';
import CallbackPage from './pages/CallbackPage';
import ChatPage from './pages/ChatPage';

const { Title, Text } = Typography;

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

  // Show loading state while processing auth (using Ant Design Spin)
  if (auth.isLoading) {
    console.log('[App] Auth is loading...');
    return (
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '100vh',
        gap: '1rem'
      }}>
        <Spin size="large" />
        <Title level={2}>DocuChat</Title>
        <Text type="secondary">Loading authentication...</Text>
        <Text type="secondary" style={{ fontSize: '0.8rem' }}>
          Active navigator: {auth.activeNavigator || 'none'}
        </Text>
      </div>
    );
  }

  // Handle OIDC errors (using Ant Design Alert and Buttons)
  if (auth.error) {
    console.error('[App] Auth error:', auth.error);
    return (
      <div style={{
        padding: '2rem',
        maxWidth: '600px',
        margin: '0 auto',
        marginTop: '4rem'
      }}>
        <Alert
          message="Authentication Error"
          description={auth.error.message}
          type="error"
          showIcon
          style={{ marginBottom: '1rem' }}
        />
        <pre style={{
          background: '#f5f5f5',
          padding: '1rem',
          borderRadius: '6px',
          overflow: 'auto',
          fontSize: '0.8rem',
          marginBottom: '1rem'
        }}>
          {JSON.stringify(auth.error, null, 2)}
        </pre>
        <Space>
          <Button
            type="primary"
            icon={<ReloadOutlined />}
            onClick={() => {
              console.log('[App] Retrying signin redirect...');
              auth.signinRedirect();
            }}
          >
            Try Again
          </Button>
          <Button
            icon={<ClearOutlined />}
            onClick={() => {
              console.log('[App] Clearing stale state...');
              auth.removeUser();
              window.location.href = '/';
            }}
          >
            Clear & Restart
          </Button>
        </Space>
      </div>
    );
  }

  return (
    <ConfigProvider
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: '#1677ff',
        },
      }}
    >
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/callback" element={<CallbackPage />} />
        <Route path="/chat" element={<ChatPage />} />
      </Routes>
    </ConfigProvider>
  );
}

export default App;

