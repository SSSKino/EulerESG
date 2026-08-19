"use client";

import Image from "next/image";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import type { RefCallback } from "react";
import { BarChart3, Check, HelpCircle, House, Languages, ListChecks, LogOut, PanelLeftClose, PanelLeftOpen, Repeat2, Settings, ShieldCheck, Star } from "lucide-react";
import { message, Modal, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import EulerLogo from "@/assets/Euler-Img.svg";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { clearAuth, getStoredAuth } from "@/lib/auth";
import { apiService } from "@/lib/api";
import { useAppLang } from "@/i18n/useAppLang";
import { useT } from "@/i18n/useT";
import { canCrossAnalyzeFiles, useFileStore } from "@/store/useFileStore";
import type { File } from "@/store/useFileStore";

const SIDEBAR_STORAGE_KEY = "dashboard-sidebar-collapsed";

type DashboardSidebarProps = {
  crossAnalysisNavigationSlotRef?: RefCallback<HTMLDivElement>;
};

export default function DashboardSidebar({
  crossAnalysisNavigationSlotRef,
}: DashboardSidebarProps = {}) {
  const router = useRouter();
  const pathname = usePathname() || "";
  const searchParams = useSearchParams();
  const clearFiles = useFileStore((state) => state.clearFiles);
  const files = useFileStore((state) => state.files);
  const { lang, setLang } = useAppLang();
  const { t } = useT();
  const [collapsed, setCollapsed] = useState(false);
  const [displayName, setDisplayName] = useState("User");
  const [selectorMode, setSelectorMode] = useState<"compliance" | "cross" | "disclosure" | null>(null);
  const [selectedReportKeys, setSelectedReportKeys] = useState<string[]>([]);

  const readyReports = useMemo(
    () => files.filter((file) => file.status === "ready" && Boolean(file.file_id)),
    [files]
  );
  const reportKey = (file: (typeof readyReports)[number]) =>
    `${file.file_id}::${file.analysis_scope_key || ""}`;
  const selectedReports = readyReports.filter((file) => selectedReportKeys.includes(reportKey(file)));
  const isMultiReportSelector = selectorMode === "cross" || selectorMode === "disclosure";
  const selectorColumns: ColumnsType<File> = [
    { title: t("files.columns.name"), dataIndex: "name", key: "name", ellipsis: true },
    { title: t("files.columns.size"), dataIndex: "size", key: "size", width: 100 },
    { title: t("files.columns.dateUploaded"), dataIndex: "dateUploaded", key: "dateUploaded", width: 120 },
    { title: t("files.columns.type"), dataIndex: "type", key: "type", width: 85 },
    {
      title: t("files.columns.framework"),
      dataIndex: "framework",
      key: "framework",
      width: 110,
      render: (value?: string) => <Tag>{value || t("common.unknown")}</Tag>,
    },
    {
      title: t("files.columns.industry"),
      key: "option",
      width: 150,
      ellipsis: true,
      render: (_value, file) => {
        const framework = (file.framework || "").trim();
        return (framework === "CDP" || framework === "TCFD" ? file.semiIndustry : file.industry) || t("common.unknown");
      },
    },
    {
      title: t("files.columns.subOption"),
      dataIndex: "semiIndustry",
      key: "subOption",
      width: 150,
      ellipsis: true,
      render: (value, file) => {
        const framework = (file.framework || "").trim();
        return framework === "CDP" || framework === "TCFD" ? t("common.na") : value || t("common.unknown");
      },
    },
    {
      title: t("files.columns.status"),
      key: "status",
      width: 100,
      render: () => <Tag color="success">{t("files.status.ready")}</Tag>,
    },
  ];

  useEffect(() => {
    const stored = window.localStorage.getItem(SIDEBAR_STORAGE_KEY);
    setCollapsed(stored === null ? window.innerWidth < 768 : stored === "true");

    const auth = getStoredAuth();
    if (auth?.name || auth?.email) setDisplayName(auth.name || auth.email || "User");
  }, []);

  useEffect(() => {
    router.prefetch("/dashboard/chat");
    router.prefetch("/dashboard/favourite");
    router.prefetch("/cross-analysis");
  }, [router]);

  const initials = useMemo(() => {
    const firstCharacter = displayName.trim().slice(0, 1);
    return (firstCharacter || "U").toUpperCase();
  }, [displayName]);

  const toggleCollapsed = () => {
    setCollapsed((current) => {
      const next = !current;
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next));
      return next;
    });
  };

  const handleLogout = () => {
    clearAuth();
    clearFiles();
    router.push("/login");
  };

  const handleSwitchAccount = () => {
    clearAuth();
    clearFiles();
    router.push("/login?switch_account=1");
  };

  const showUnavailableMessage = (feature: "settings" | "help") => {
    const label = feature === "settings"
      ? (lang === "zh" ? "设置" : "Settings")
      : (lang === "zh" ? "帮助" : "Help");
    void message.info(lang === "zh" ? `${label}功能即将开放` : `${label} is coming soon`);
  };

  const openReportSelector = (mode: "compliance" | "cross" | "disclosure") => {
    setSelectorMode(mode);
    setSelectedReportKeys([]);
    if (files.length === 0) {
      void useFileStore.getState().loadFilesFromBackend({ showLoading: false });
    }
  };

  const confirmReportSelection = () => {
    if (selectorMode === "compliance") {
      const report = selectedReports[0];
      if (!report?.file_id) {
        void message.info(lang === "zh" ? "请选择一份已处理完成的报告" : "Select one processed report");
        return;
      }
      let target = `/dashboard/chat?file_id=${encodeURIComponent(report.file_id)}`;
      if (report.analysis_scope_key) target += `&scope=${encodeURIComponent(report.analysis_scope_key)}`;
      apiService.prefetchAssessmentByFile(
        report.file_id,
        report.analysis_scope_key,
        false,
        true,
      );
      setSelectorMode(null);
      router.push(target);
      return;
    }

    const uniqueFileIds = [...new Set(selectedReports.map((file) => file.file_id).filter(Boolean) as string[])];
    if (uniqueFileIds.length < 2) {
      void message.info(lang === "zh" ? "请至少选择两份不同的已处理报告" : "Select at least two different processed reports");
      return;
    }
    if (!canCrossAnalyzeFiles(selectedReports)) {
      void message.warning(lang === "zh" ? "所选报告的框架或分析范围不兼容" : "The selected reports are not compatible for cross analysis");
      return;
    }
    if (selectorMode === "disclosure") {
      void apiService.getCrossAnalysisReports(uniqueFileIds).catch(() => undefined);
      uniqueFileIds.forEach((fileId) => apiService.prefetchAssessmentByFile(fileId));
    } else {
      apiService.prefetchCrossAnalysis(uniqueFileIds);
    }
    setSelectorMode(null);
    const viewParam = selectorMode === "disclosure" ? "&view=disclosure" : "";
    router.push(`/cross-analysis?ids=${encodeURIComponent(uniqueFileIds.join(","))}${viewParam}`);
  };

  const isHomepage = pathname === "/dashboard";
  const isCompliance = pathname.startsWith("/dashboard/chat") || pathname.startsWith("/dashboard/company");
  const isCrossAnalysisRoute = pathname.startsWith("/cross-analysis");
  const isDisclosureCompleteness =
    isCrossAnalysisRoute && (searchParams.get("view") || "").trim().toLowerCase() === "disclosure";
  const isCrossAnalysis = isCrossAnalysisRoute && !isDisclosureCompleteness;
  const isFavourite = pathname.startsWith("/dashboard/favourite");
  const navigationClass = (active: boolean) =>
    `flex h-10 w-full shrink-0 items-center rounded-xl transition-colors ${
      active
        ? "bg-[#ececec] text-slate-900 hover:bg-[#e5e5e5]"
        : "text-slate-700 hover:bg-[#ececec] hover:text-slate-950"
    } ${collapsed ? "justify-center px-2" : "gap-3 px-2.5"}`;

  return (
    <>
    <div
      aria-hidden="true"
      className={`h-screen shrink-0 transition-[width] duration-200 ease-[var(--motion-fluid)] ${collapsed ? "w-[60px]" : "w-[260px]"}`}
    />
    <aside
      className={`fixed bottom-0 left-0 top-0 z-40 flex h-dvh shrink-0 flex-col overflow-hidden bg-[#f9f9f9] transition-[width] duration-200 ease-[var(--motion-fluid)] ${
        collapsed ? "w-[60px]" : "w-[260px]"
      }`}
      data-collapsed={collapsed}
    >
      <div className={`flex h-14 shrink-0 items-center ${collapsed ? "justify-center px-2" : "justify-between px-2.5"}`}>
        {collapsed ? (
          <button
            type="button"
            onClick={toggleCollapsed}
            className="group relative flex h-10 w-10 items-center justify-center rounded-xl text-slate-600 transition-colors hover:bg-[#ececec] hover:text-slate-950"
            aria-label="Expand sidebar"
            title="Expand sidebar"
            aria-expanded={!collapsed}
          >
            <Image
              src={EulerLogo}
              alt="Euler ESG"
              className="h-6 w-6 transition-[transform,opacity] duration-150 ease-[var(--motion-fluid)] group-hover:scale-75 group-hover:opacity-0 group-focus-visible:scale-75 group-focus-visible:opacity-0"
            />
            <PanelLeftOpen className="absolute h-5 w-5 scale-75 opacity-0 transition-[transform,opacity] duration-150 ease-[var(--motion-fluid)] group-hover:scale-100 group-hover:opacity-100 group-focus-visible:scale-100 group-focus-visible:opacity-100" />
          </button>
        ) : (
          <>
            <button
              type="button"
              onClick={() => router.push("/dashboard")}
              className="flex h-10 min-w-0 items-center gap-2.5 rounded-xl bg-transparent px-2 text-left hover:bg-[#ececec]"
              aria-label={t("nav.goToAllFiles")}
            >
              <Image src={EulerLogo} alt="Euler ESG" className="h-7 w-7 shrink-0" />
              <span className="truncate text-[18px] font-semibold leading-5 text-[#2274BC]">Euler ESG</span>
            </button>
            <button
              type="button"
              onClick={toggleCollapsed}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-slate-600 transition-colors hover:bg-[#ececec] hover:text-slate-950"
              aria-label="Collapse sidebar"
              title="Collapse sidebar"
              aria-expanded={!collapsed}
            >
              <PanelLeftClose className="h-5 w-5" />
            </button>
          </>
        )}
      </div>

      <nav className={`flex min-h-0 flex-1 flex-col gap-1 ${collapsed ? "px-2 pt-1" : "px-2.5 pt-1"}`}>
        <button
          type="button"
          onClick={() => router.push("/dashboard")}
          className={navigationClass(isHomepage)}
          title={collapsed ? "Homepage" : undefined}
        >
          <House className={`h-[18px] w-[18px] shrink-0 ${isHomepage ? "text-[#2274BC]" : ""}`} />
          {!collapsed && <span className="truncate text-sm">Homepage</span>}
        </button>
        <button
          type="button"
          onClick={() => openReportSelector("compliance")}
          className={navigationClass(isCompliance)}
          title={collapsed ? "Compliance" : undefined}
        >
          <ShieldCheck className={`h-[18px] w-[18px] shrink-0 ${isCompliance ? "text-[#2274BC]" : ""}`} />
          {!collapsed && <span className="truncate text-sm">Compliance</span>}
        </button>
        <button
          type="button"
          onClick={() => openReportSelector("cross")}
          className={navigationClass(isCrossAnalysis)}
          title={collapsed ? "Cross Analysis" : undefined}
          aria-current={isCrossAnalysis ? "page" : undefined}
        >
          <BarChart3 className={`h-[18px] w-[18px] shrink-0 ${isCrossAnalysisRoute ? "text-[#2274BC]" : ""}`} />
          {!collapsed && <span className="truncate text-sm">Cross Analysis</span>}
        </button>
        <div
          role="group"
          aria-label="Cross Analysis"
          data-testid="cross-analysis-subnavigation"
          className={`flex min-h-0 flex-col ${isCrossAnalysis && !collapsed ? "flex-1" : ""}`}
        >
          <button
            type="button"
            data-testid="disclosure-completeness-nav"
            onClick={() => openReportSelector("disclosure")}
            aria-label={t("crossAnalysis.disclosureCompleteness")}
            aria-current={isDisclosureCompleteness ? "page" : undefined}
            className={`flex shrink-0 items-center border border-transparent transition-colors ${
              isDisclosureCompleteness
                ? "bg-[#ececec] text-slate-900 hover:bg-[#e5e5e5]"
                : "text-slate-600 hover:bg-[#ececec] hover:text-slate-950"
            } ${
              collapsed
                ? "h-9 w-full justify-center rounded-lg px-2"
                : "ml-5 w-[calc(100%-1.25rem)] rounded-xl px-3 py-2 text-left"
            }`}
            title={collapsed ? t("crossAnalysis.disclosureCompleteness") : undefined}
          >
            {collapsed ? (
              <ListChecks className={`h-4 w-4 shrink-0 ${isDisclosureCompleteness ? "text-[#2274BC]" : ""}`} />
            ) : (
              <span className="truncate text-sm font-medium">{t("crossAnalysis.disclosureCompleteness")}</span>
            )}
          </button>
          {isCrossAnalysis ? (
            <div
              ref={crossAnalysisNavigationSlotRef}
              data-testid="cross-analysis-navigation-slot"
              hidden={collapsed}
              className="min-h-0 flex-1 overflow-y-auto"
            />
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => router.push("/dashboard/favourite")}
          className={navigationClass(isFavourite)}
          title={collapsed ? "Favourite" : undefined}
          aria-current={isFavourite ? "page" : undefined}
        >
          <Star className={`h-[18px] w-[18px] shrink-0 ${isFavourite ? "text-[#2274BC]" : ""}`} />
          {!collapsed && <span className="truncate text-sm">Favourite</span>}
        </button>
      </nav>

      <div className={`${collapsed ? "p-2 pb-2" : "p-2.5 pb-2.5"}`}>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              className={`h-12 w-full rounded-xl p-2 hover:bg-[#ececec] ${collapsed ? "justify-center" : "justify-start gap-2.5"}`}
              aria-label={t("nav.userMenu")}
            >
              <Avatar className="h-8 w-8 shrink-0">
                <AvatarFallback>{initials}</AvatarFallback>
              </Avatar>
              {!collapsed && <span className="min-w-0 truncate text-sm font-medium text-slate-700">{displayName}</span>}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            side={collapsed ? "right" : "top"}
            align="start"
            sideOffset={8}
            className="w-[240px] rounded-2xl border-slate-200 p-2 shadow-xl"
          >
            <DropdownMenuLabel className="flex items-center gap-3 px-2 py-2 font-normal">
              <Avatar className="h-8 w-8 shrink-0">
                <AvatarFallback>{initials}</AvatarFallback>
              </Avatar>
              <span className="min-w-0 truncate text-sm font-medium text-slate-800">{displayName}</span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="h-10 rounded-lg" onClick={() => showUnavailableMessage("settings")}>
              <Settings className="mr-2 h-[18px] w-[18px]" />
              <span>{t("nav.settings")}</span>
            </DropdownMenuItem>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger className="h-10 rounded-lg">
                <Languages className="mr-2 h-[18px] w-[18px]" />
                <span>{lang === "zh" ? "语言" : "Language"}</span>
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="min-w-[150px] rounded-xl p-1.5">
                <DropdownMenuItem className="h-9 rounded-lg" onClick={() => setLang("zh")}>
                  <span className="flex-1">中文</span>
                  {lang === "zh" && <Check className="h-4 w-4" />}
                </DropdownMenuItem>
                <DropdownMenuItem className="h-9 rounded-lg" onClick={() => setLang("en")}>
                  <span className="flex-1">English</span>
                  {lang === "en" && <Check className="h-4 w-4" />}
                </DropdownMenuItem>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuItem className="h-10 rounded-lg" onClick={() => showUnavailableMessage("help")}>
              <HelpCircle className="mr-2 h-[18px] w-[18px]" />
              <span>{lang === "zh" ? "帮助" : "Help"}</span>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="h-10 rounded-lg" onClick={handleSwitchAccount}>
              <Repeat2 className="mr-2 h-[18px] w-[18px]" />
              <span>{lang === "zh" ? "切换账号" : "Switch account"}</span>
            </DropdownMenuItem>
            <DropdownMenuItem className="h-10 rounded-lg" onClick={handleLogout}>
              <LogOut className="mr-2 h-[18px] w-[18px]" />
              <span>{t("nav.logout")}</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <Modal
        open={selectorMode !== null}
        title={selectorMode === "disclosure"
          ? (lang === "zh" ? "选择披露完整度报告" : "Select reports for disclosure completeness")
          : selectorMode === "cross"
            ? (lang === "zh" ? "选择综合分析报告" : "Select reports for cross analysis")
            : (lang === "zh" ? "选择合规分析报告" : "Select a report for compliance analysis")}
        okText={lang === "zh" ? "开始分析" : "Start analysis"}
        cancelText={t("common.cancel")}
        onOk={confirmReportSelection}
        onCancel={() => setSelectorMode(null)}
        okButtonProps={{ disabled: isMultiReportSelector ? selectedReportKeys.length < 2 : selectedReportKeys.length !== 1 }}
        width={1100}
        destroyOnClose
      >
        <Table<File>
          className="dashboard-file-table"
          columns={selectorColumns}
          dataSource={readyReports}
          rowKey={reportKey}
          size="small"
          scroll={{ x: 1000 }}
          locale={{ emptyText: lang === "zh" ? "暂无已处理完成的报告" : "No processed reports available" }}
          pagination={{ pageSize: 8, showSizeChanger: false, hideOnSinglePage: true }}
          rowSelection={{
            type: isMultiReportSelector ? "checkbox" : "radio",
            selectedRowKeys: selectedReportKeys,
            onChange: (keys) => setSelectedReportKeys(keys.map(String)),
          }}
          onRow={(file) => ({
            onClick: () => {
              const key = reportKey(file);
              if (!isMultiReportSelector) {
                apiService.prefetchAssessmentByFile(
                  file.file_id,
                  file.analysis_scope_key,
                  false,
                  true,
                );
                setSelectedReportKeys([key]);
                return;
              }
              setSelectedReportKeys((current) =>
                current.includes(key) ? current.filter((item) => item !== key) : [...current, key]
              );
            },
            className: "cursor-pointer",
          })}
        />
      </Modal>
    </aside>
    </>
  );
}
