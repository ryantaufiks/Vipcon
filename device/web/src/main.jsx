import { createRoot } from 'react-dom/client'
import '@fontsource-variable/inter'
import App from './App.jsx'
import { ThemeProvider, ToastProvider, ConfirmProvider } from './ui.jsx'
import './theme.css'

createRoot(document.getElementById('root')).render(
  <ThemeProvider>
    <ToastProvider>
      <ConfirmProvider>
        <App />
      </ConfirmProvider>
    </ToastProvider>
  </ThemeProvider>,
)
