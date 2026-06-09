import { RunView } from "@/components/RunView";
import { getRun } from "@/lib/api";

export default async function RunPage({params}: {params: Promise<{runId: string}>}) {
  const {runId} = await params;
  const run = await getRun(runId);
  return <RunView run={run} />;
}
