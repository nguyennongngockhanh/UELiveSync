#pragma once

#include "CoreMinimal.h"
#include "Widgets/SCompoundWidget.h"
#include "Widgets/Text/STextBlock.h"

class UUELiveSyncSubsystem;

class SLiveSyncStatusWidget : public SCompoundWidget
{
public:

    SLATE_BEGIN_ARGS(SLiveSyncStatusWidget) {}
    SLATE_END_ARGS()

    void Construct(const FArguments& InArgs);

    virtual void Tick(
        const FGeometry& AllottedGeometry,
        const double InCurrentTime,
        const float InDeltaTime) override;

    virtual bool SupportsKeyboardFocus() const override
    {
        return false;
    }

private:

    FLinearColor GetStatusColor() const;

    FText GetStatusTooltip() const;

    TWeakObjectPtr<UUELiveSyncSubsystem>
        WeakSubsystem;

    TSharedPtr<STextBlock>
        StatusDot;

    TSharedPtr<STextBlock>
        UptimeBlock;

    TSharedPtr<STextBlock>
        ObjectsBlock;

    TSharedPtr<STextBlock>
        QueueBlock;

    TSharedPtr<STextBlock>
        PacketBlock;
};
