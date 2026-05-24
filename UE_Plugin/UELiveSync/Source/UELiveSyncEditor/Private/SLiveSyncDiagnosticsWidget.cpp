#include "SLiveSyncDiagnosticsWidget.h"

#include "UELiveSyncSubsystem.h"

#include "Editor.h"
#include "LevelEditor.h"

#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Text/STextBlock.h"
#include "Styling/StyleColors.h"

#define LOCTEXT_NAMESPACE "SLiveSyncDiagnosticsWidget"


UUELiveSyncSubsystem*
FindDiagnosticsSubsystem()
{
    if (!GEditor)
    {
        return nullptr;
    }

    UWorld* World = nullptr;

    if (GEditor->PlayWorld)
    {
        World = GEditor->PlayWorld;
    }
    else
    {
        World =
            GEditor->
            GetEditorWorldContext()
            .World();
    }

    if (!World)
    {
        return nullptr;
    }

    return
        World->GetSubsystem<
            UUELiveSyncSubsystem>();
}


void SLiveSyncDiagnosticsWidget::
Construct(
    const FArguments& InArgs)
{
    WeakSubsystem =
        FindDiagnosticsSubsystem();

    ChildSlot
    [
        SNew(SBorder)
        .BorderImage(
            FAppStyle::Get().GetBrush(
                "ToolPanel.DarkGroupBorder"))
        .Padding(10.0f)
        [
            SNew(SScrollBox)
            + SScrollBox::Slot()
            [
                SAssignNew(
                    ContentBlock,
                    STextBlock)
                .Text(
                    LOCTEXT(
                        "Initializing",
                        "Initializing diagnostics..."))
                .Font(
                    FAppStyle::Get().GetFontStyle(
                        "SmallFont"))
            ]
        ]
    ];
}


void SLiveSyncDiagnosticsWidget::Tick(
    const FGeometry& AllottedGeometry,
    const double InCurrentTime,
    const float InDeltaTime)
{
    SCompoundWidget::Tick(
        AllottedGeometry,
        InCurrentTime,
        InDeltaTime);

    // Throttle refresh to ~250ms
    if (InCurrentTime - LastRefreshTime < 0.25)
    {
        return;
    }

    LastRefreshTime = InCurrentTime;

    RefreshText();
}


void SLiveSyncDiagnosticsWidget::
RefreshText()
{
    UUELiveSyncSubsystem* Subsystem =
        WeakSubsystem.Get();

    if (!Subsystem)
    {
        Subsystem =
            FindDiagnosticsSubsystem();

        WeakSubsystem = Subsystem;
    }

    if (!Subsystem)
    {
        ContentBlock->SetText(
            LOCTEXT(
                "NoData",
                "Live Sync: subsystem not available"));

        return;
    }

    FString Report;

    // Connection
    FText ConnText =
        Subsystem->GetConnectionStatusText();

    bool bConnected =
        ConnText.ToString() ==
        TEXT("Connected");

    Report +=
        FString::Printf(
            TEXT("=== Live Sync Diagnostics ===\n\n"),
            *ConnText.ToString());

    Report +=
        FString::Printf(
            TEXT("Status: %s\n"),
            bConnected
                ? TEXT("CONNECTED")
                : TEXT("DISCONNECTED"));

    if (bConnected)
    {
        Report +=
            FString::Printf(
                TEXT("Uptime: %s\n"),
                *Subsystem->GetUptimeText().ToString());
    }

    Report +=
        FString::Printf(
            TEXT("\n[Objects]\n"));

    Report +=
        FString::Printf(
            TEXT("  Tracked: %s\n"),
            *Subsystem->GetObjectsTrackedText().ToString());

    Report +=
        FString::Printf(
            TEXT("\n[Queue]\n"));

    Report +=
        FString::Printf(
            TEXT("  Current: %s\n"),
            *Subsystem->GetQueueDepthText().ToString());

    // Build full diagnostics text via subsystem
    FText DiagText =
        Subsystem->GetDiagnosticsText();

    Report +=
        TEXT("\n") +
        DiagText.ToString();

    ContentBlock->SetText(
        FText::FromString(Report));
}


#undef LOCTEXT_NAMESPACE
