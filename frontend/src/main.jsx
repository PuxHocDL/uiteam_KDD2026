import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { ConfirmProvider } from './components/common/ConfirmDialog';
import { PromptProvider } from './components/common/PromptDialog';
import { ToastProvider } from './components/common/Toast';
import { AuthProvider } from './components/common/AuthProvider';
import './styles/theme.css';
import './styles/app.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ToastProvider>
      <ConfirmProvider>
        <PromptProvider>
          <AuthProvider>
            <App />
          </AuthProvider>
        </PromptProvider>
      </ConfirmProvider>
    </ToastProvider>
  </React.StrictMode>
);
