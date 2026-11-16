export const metadata = {
  title: 'Livestream AI Multi-Agent Dashboard',
  description: 'Multi-Agent, Vector RAG, and Tool-Calling Console for Livestream Sales',
};

export default function RootLayout({ children }) {
  return (
    <html lang="vi">
      <body style={{ margin: 0, background: '#0f172a', color: '#f8fafc', fontFamily: 'system-ui, sans-serif' }}>
        {children}
      </body>
    </html>
  );
}
