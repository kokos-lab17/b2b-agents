import './globals.css';
import type { Metadata } from 'next';
import 'ag-grid-community/styles/ag-grid.css';
import 'ag-grid-community/styles/ag-theme-alpine.css';

export const metadata: Metadata = {
  title: 'Quoto AI — Smarter Negotiations. Better Deals.',
  description: 'An autonomous multi-agent procurement platform where an AI buyer negotiates with independent supplier agents, evaluates commercial offers, selects the best overall deal and facilitates buyer-authorized PayPal Sandbox payments.',
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
