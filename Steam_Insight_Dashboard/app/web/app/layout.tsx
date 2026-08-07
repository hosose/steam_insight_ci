import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Steam Insight Dashboard",
  description: "Steam Insight Platform Dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body>
        {children}
      </body>
    </html>
  );
}
