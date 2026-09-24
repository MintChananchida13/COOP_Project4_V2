import AdminProcessingLogDetailPage from "../../../../admin/AdminProcessingLogDetailPage";

export default async function ProcessingLogDetailRoute({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AdminProcessingLogDetailPage logId={id} />;
}
