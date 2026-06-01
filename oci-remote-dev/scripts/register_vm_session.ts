#!/usr/bin/env bun
/**
 * register_vm_session.ts — register a Cloud DevVM agentctl session as a PAI
 * remote-code session with an `ssh-vm` input adapter, so the same Telegram/
 * WhatsApp bots can drive it. Federation F0 (see docs/REMOTE-CONTROL-BRIDGE.md).
 *
 * This is a thin, explicit wrapper over the live PAI registry so you don't have to
 * hand-craft the adapter shape. It imports the live registry by absolute path.
 *
 *   bun register_vm_session.ts \
 *     --id vm-myproj-claude \
 *     --channel telegram:123456 \
 *     --ssh-host devvm \
 *     --tmux-target agent:myproj:claude \
 *     --cwd /Volumes/ExternalNVME/GitHub/devvm \
 *     [--title "VM: myproj"]
 *
 * The adapter target is "<ssh-host>|<tmux-target>". Your ~/.ssh/config must define
 * the host alias. The session is registered with write capability.
 */
import { homedir } from "node:os";
import { join } from "node:path";

function arg(name: string, fallback = ""): string {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

async function main(): Promise<number> {
  const id = arg("id");
  const channel = arg("channel");
  const sshHost = arg("ssh-host");
  const tmuxTarget = arg("tmux-target");
  const cwd = arg("cwd", process.cwd());
  const title = arg("title", `VM: ${id}`);

  if (!id || !channel || !sshHost || !tmuxTarget) {
    console.error("usage: register_vm_session.ts --id <id> --channel <telegram:chatid> --ssh-host <alias> --tmux-target <agent:proj:agent> [--cwd <dir>] [--title <t>]");
    return 2;
  }

  const target = `${sshHost}|${tmuxTarget}`;
  // Import the live PAI registry (absolute path; this script ships in the devvm repo
  // but registers into the user's live ~/.claude/PAI registry).
  const registryPath = join(homedir(), ".claude", "PAI", "TOOLS", "RemoteCodeSessionRegistry.ts");
  const { registerSession } = await import(registryPath);

  const res = registerSession({
    id,
    title,
    cwd,
    channelTarget: channel,
    monitorSource: { kind: "ssh-vm", target, label: "Cloud DevVM agentctl session" },
    inputAdapter: { kind: "ssh-vm", target, capabilities: ["write"] },
  });

  if (!res.ok) {
    console.error(`registration failed: ${res.code} — ${res.reason}`);
    return 1;
  }
  console.log(`registered ssh-vm session '${id}' → ${target} for ${channel}`);
  console.log("In Telegram/WhatsApp, select this session and type a reply to drive the VM agent.");
  return 0;
}

main().then((code) => process.exit(code));
