import { ChatShell } from "../components/chat-shell";

export default function HomePage() {
  const apiBaseUrl =
    process.env.QANORM_PUBLIC_API_BASE_URL ??
    process.env.NEXT_PUBLIC_QANORM_API_BASE_URL ??
    "http://localhost:8000";

  return <ChatShell apiBaseUrl={apiBaseUrl} />;
}
