import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Music Lens — 음악 분석실",
  description: "음악의 박자, 음높이와 편곡을 시간 위에서 탐색하는 팀의 음악 분석실.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body className="antialiased">{children}</body>
    </html>
  );
}
