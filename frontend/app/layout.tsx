import './globals.css';
import type { Metadata } from 'next';
import 'ag-grid-community/styles/ag-grid.css';
import 'ag-grid-community/styles/ag-theme-alpine.css';

export const metadata: Metadata = {
  title: 'Autonomous B2B Multi-Agent Procurement & Negotiation',
  description: 'Autonomous AI agents negotiate multi-variable commercial contracts and settle via PayPal Sandbox.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="font-sans antialiased bg-gray-950 text-white min-h-screen">
        {children}
      </body>
    </html>
  );
}
