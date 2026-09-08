import AdminRequestDetailPage from "../../../../../admin/AdminRequestDetailPage";

export default async function TemplateEditRoute({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AdminRequestDetailPage templateId={id} />;
}
