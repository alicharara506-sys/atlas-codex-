import { FundOsConsole } from "../components/FundOsConsole.tsx";

export default function ConsolePage() {
  return <main className="terminal-shell standalone-console">
    <form action="/"><button className="console-back" type="submit">← BACK TO SIGNAL TERMINAL</button></form>
    <FundOsConsole />
  </main>;
}
