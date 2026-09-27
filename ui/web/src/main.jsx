import React from 'react';
import { createRoot } from 'react-dom/client';
import '@phosphor-icons/web/duotone';
import '@phosphor-icons/web/fill';
import './styles/broadsheet.css';
import './styles/app.css';
import App from './App.jsx';

createRoot(document.getElementById('root')).render(<App />);
