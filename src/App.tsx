import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from '@/components/ui/toaster'
import { Toaster as Sonner } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import Index from './pages/Index'
import NotFound from './pages/NotFound'
import Layout from './components/Layout'
import Login from './pages/Login'
import Downloaders from './pages/Downloaders'
import Executions from './pages/Executions'
import ExecutionDetail from './pages/ExecutionDetail'
import Schedules from './pages/Schedules'
import Settings from './pages/Settings'
import Calendar from './pages/Calendar'
import { AuthProvider } from './hooks/use-auth'
import { ThemeProvider } from './hooks/use-theme'

const App = () => (
  <AuthProvider>
    <ThemeProvider>
      <BrowserRouter>
        <TooltipProvider>
          <Toaster />
          <Sonner />
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/signup" element={<Navigate to="/login" replace />} />
            <Route element={<Layout />}>
              <Route path="/" element={<Index />} />
              <Route path="/downloaders" element={<Downloaders />} />
              <Route path="/schedules" element={<Schedules />} />
              <Route path="/executions" element={<Executions />} />
              <Route path="/calendario" element={<Calendar />} />
              <Route path="/executions/:id" element={<ExecutionDetail />} />
              <Route path="/history" element={<Navigate to="/executions" replace />} />
              <Route path="/settings" element={<Settings />} />
            </Route>
            <Route path="*" element={<NotFound />} />
          </Routes>
        </TooltipProvider>
      </BrowserRouter>
    </ThemeProvider>
  </AuthProvider>
)

export default App
