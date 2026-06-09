import { SourceEditor } from "@/components/SourceEditor";

export default async function SourcePage({params}: {params: Promise<{sourceId: string}>}) {
  const {sourceId} = await params;
  return <SourceEditor sourceId={sourceId} />;
}
