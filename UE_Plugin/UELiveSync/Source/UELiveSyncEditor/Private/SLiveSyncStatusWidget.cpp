#include "SLiveSyncStatusWidget.h"

#include "UELiveSyncSubsystem.h"

#include "Editor.h"
#include "LevelEditor.h"

#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SGridPanel.h"
#include "Widgets/Images/SImage.h"
#include "Widgets/Text/STextBlock.h"
#include "Styling/StyleColors.h"

#define LOCTEXT_NAMESPACE "SLiveSyncStatusWidget"


UUELiveSyncSubsystem*
FindSyncSubsystem()
{
    if (!GEditor)
    {
        return nullptr;
    }

    // Prefer PIE world if running
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


void SLiveSyncStatusWidget::Construct(
    const FArguments& InArgs)
{
    WeakSubsystem =
        FindSyncSubsystem();

    if (!WeakSubsystem.IsValid())
    {
        // Fallback: try again on next tick
        WeakSubsystem = nullptr;
    }

    ChildSlot
    [
        SNew(SBorder)
        .BorderImage(
            FAppStyle::Get().GetBrush(
                "ToolPanel.DarkGroupBorder"))
        .Padding(8.0f)
        [
            SNew(SGridPanel)
            .FillColumn(1, 1.0f)

            // Row 0: Status dot + label
            + SGridPanel::Slot(
                0, 0)
            .Padding(0, 0, 8, 4)
            .VAlign(VAlign_Center)
            [
                SAssignNew(StatusDot, STextBlock)
                .Text(
                    LOCTEXT(
                        "StatusDot",
                        "\u25CF LiveSync"))
                .Font(
                    FAppStyle::Get().GetFontStyle(
                        "NormalFont"))
            ]

            // Row 1: Uptime
            + SGridPanel::Slot(
                0, 1)
            .Padding(0, 2)
            [
                SNew(STextBlock)
                .Text(
                    LOCTEXT(
                        "UptimeLabel",
                        "  Uptime:"))
                .ColorAndOpacity(
                    FSlateColor(
                        FLinearColor(
                            0.6f, 0.6f, 0.6f)))
            ]

            + SGridPanel::Slot(
                1, 1)
            .Padding(8, 2, 0, 2)
            [
                SAssignNew(
                    UptimeBlock,
                    STextBlock)
                .Text(
                    LOCTEXT(
                        "UptimeValue",
                        "\u2014"))
            ]

            // Row 2: Objects
            + SGridPanel::Slot(
                0, 2)
            .Padding(0, 2)
            [
                SNew(STextBlock)
                .Text(
                    LOCTEXT(
                        "ObjectsLabel",
                        "  Objects:"))
                .ColorAndOpacity(
                    FSlateColor(
                        FLinearColor(
                            0.6f, 0.6f, 0.6f)))
            ]

            + SGridPanel::Slot(
                1, 2)
            .Padding(8, 2, 0, 2)
            [
                SAssignNew(
                    ObjectsBlock,
                    STextBlock)
                .Text(
                    LOCTEXT(
                        "ObjectsValue",
                        "0"))
            ]

            // Row 3: Queue
            + SGridPanel::Slot(
                0, 3)
            .Padding(0, 2)
            [
                SNew(STextBlock)
                .Text(
                    LOCTEXT(
                        "QueueLabel",
                        "  Queue:"))
                .ColorAndOpacity(
                    FSlateColor(
                        FLinearColor(
                            0.6f, 0.6f, 0.6f)))
            ]

            + SGridPanel::Slot(
                1, 3)
            .Padding(8, 2, 0, 2)
            [
                SAssignNew(
                    QueueBlock,
                    STextBlock)
                .Text(
                    LOCTEXT(
                        "QueueValue",
                        "0"))
            ]

            // Row 4: Last packet
            + SGridPanel::Slot(
                0, 4)
            .Padding(0, 2)
            [
                SNew(STextBlock)
                .Text(
                    LOCTEXT(
                        "PacketLabel",
                        "  Last pkt:"))
                .ColorAndOpacity(
                    FSlateColor(
                        FLinearColor(
                            0.6f, 0.6f, 0.6f)))
            ]

            + SGridPanel::Slot(
                1, 4)
            .Padding(8, 2, 0, 2)
            [
                SAssignNew(
                    PacketBlock,
                    STextBlock)
                .Text(
                    LOCTEXT(
                        "PacketValue",
                        "\u2014"))
            ]
        ]
    ];
}


void SLiveSyncStatusWidget::Tick(
    const FGeometry& AllottedGeometry,
    const double InCurrentTime,
    const float InDeltaTime)
{
    SCompoundWidget::Tick(
        AllottedGeometry,
        InCurrentTime,
        InDeltaTime);

    UUELiveSyncSubsystem* Subsystem =
        WeakSubsystem.Get();

    if (!Subsystem)
    {
        // Retry finding the subsystem
        Subsystem =
            FindSyncSubsystem();

        WeakSubsystem =
            Subsystem;
    }

    if (!Subsystem)
    {
        StatusDot->SetText(
            LOCTEXT(
                "NoSubsystem",
                "\u25CF No Sync"));

        StatusDot->SetColorAndOpacity(
            FSlateColor(
                FLinearColor(
                    0.5f, 0.5f, 0.5f)));

        return;
    }

    // Update status dot color
    FLinearColor DotColor =
        GetStatusColor();

    FText StatusLabel;

    bool bConnected =
        Subsystem->
        GetConnectionStatusText()
        .ToString() == TEXT("Connected");

    if (bConnected)
    {
        StatusLabel =
            LOCTEXT(
                "StatusConnected",
                "\u25CF LiveSync");
    }
    else
    {
        StatusLabel =
            LOCTEXT(
                "StatusDisconnected",
                "\u25CF LiveSync");
    }

    StatusDot->SetText(StatusLabel);

    StatusDot->SetColorAndOpacity(
        FSlateColor(DotColor));

    StatusDot->SetToolTipText(
        GetStatusTooltip());

    // Update data rows
    UptimeBlock->SetText(
        Subsystem->GetUptimeText());

    ObjectsBlock->SetText(
        Subsystem->
        GetObjectsTrackedText());

    QueueBlock->SetText(
        Subsystem->
        GetQueueDepthText());

    PacketBlock->SetText(
        Subsystem->
        GetLastPacketTimeText());
}


FLinearColor
SLiveSyncStatusWidget::
GetStatusColor() const
{
    UUELiveSyncSubsystem* Subsystem =
        WeakSubsystem.Get();

    if (!Subsystem)
    {
        return FLinearColor(
            0.5f, 0.5f, 0.5f);
    }

    bool bConnected =
        Subsystem->
        GetConnectionStatusText()
        .ToString() == TEXT("Connected");

    if (bConnected)
    {
        return FLinearColor(
            0.0f, 0.8f, 0.0f);
    }

    return FLinearColor(
        0.8f, 0.0f, 0.0f);
}


FText
SLiveSyncStatusWidget::
GetStatusTooltip() const
{
    UUELiveSyncSubsystem* Subsystem =
        WeakSubsystem.Get();

    if (!Subsystem)
    {
        return
            LOCTEXT(
                "TooltipNoSubsystem",
                "Live Sync: world not available");
    }

    FText ConnText =
        Subsystem->
        GetConnectionStatusText();

    FText UptimeText =
        Subsystem->GetUptimeText();

    FText ObjectsText =
        Subsystem->
        GetObjectsTrackedText();

    FText QueueText =
        Subsystem->
        GetQueueDepthText();

    return
        FText::Format(
            LOCTEXT(
                "TooltipFormat",
                "Live Sync\nStatus: {0}\nUptime: {1}\nObjects: {2}\nQueue: {3}"),
            ConnText,
            UptimeText,
            ObjectsText,
            QueueText);
}


#undef LOCTEXT_NAMESPACE
