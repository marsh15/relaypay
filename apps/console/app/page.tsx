import { redirect } from "next/navigation";
import { getSession } from "@/lib/server-api";

export default async function HomePage() {
  redirect((await getSession()) ? "/lab" : "/login");
}
