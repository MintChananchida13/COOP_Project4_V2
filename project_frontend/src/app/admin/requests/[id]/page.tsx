import AdminRequestDetailPage from "../../../../admin/AdminRequestDetailPage";

export default async function RequestDetailRoute({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AdminRequestDetailPage requestId={id} />;
}
