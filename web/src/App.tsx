import { ChatShell } from "./components/ChatShell";
import { ConversationProvider } from "./store/ConversationProvider";

export default function App() {
  return (
    <ConversationProvider>
      <ChatShell />
    </ConversationProvider>
  );
}
