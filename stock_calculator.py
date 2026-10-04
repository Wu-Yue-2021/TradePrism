print("简易加减仓计算器\n")
print("""#注意
#卖出时使用的是调整成本法而非加权移动平均法，即按当前价格从总成本中扣除卖出份额，然后计算新的总成本和平均成本；
#而不是按成本价扣除卖出份额。这相当于把卖出实现的盈利或亏损分摊到剩余份额上，用卖出总金额覆盖或补偿原始成本的一部分。
#因此,可能出现目标均价过低或过高，需要卖出比持仓更多的份额来分摊的情况。
#此时做空后的负成本是卖掉所借份额的暂时所得，目标均价是买入归还的盈亏平衡线（反向，越高越容易实现）。\n""")
#经过我一下午的数学推导，目标均价在现价和成本价之间时买入，目标均价与现价相对于成本同向且超出范围则必须做空，否则卖出

PRACTICE=True#是否具有实际意义（否则是用于数学研究）

print("跟踪标的：",end='')
name=input()
import os
os.system(f'title {name}_计算器')
os.system('cls')
while True:
    print("模式1：给定变动份额，计算变动后均价\n模式2：给定目标均价，计算变动份额")
    print("模式3：调整当前价格；模式4：调整变动份额；模式5：调整目标均价")

    print("请输入模式：",end="")
    mode_input=int(input())
    print("------------------")
    try:
        if mode_input==1:
            Mode=1
            print("均摊成本：")
            avg_cost=float(input())
            print("当前份额（手）：")
            num_now=int(input())*100
            volume=num_now*avg_cost

            print("变动份额（手）：")
            change_num=int(input())*100
            print("当前价格：")
            cur_price=float(input())

        elif mode_input==2:
            Mode=2
            print("当前份额（手）：")
            num_now=int(input())*100
            print("均摊成本：")
            avg_cost=float(input())
            volume=num_now*avg_cost

            print("目标均价：")
            target_price=float(input())
            print("当前价格：")
            cur_price=float(input())

            #模式2独有计算
            if target_price != cur_price:
                change_num = int((target_price * num_now - volume) / (cur_price - target_price))
                #四舍五入转成100的倍数
                if PRACTICE:
                    change_num=round(change_num / 100) * 100
            else:
                change_num = float("inf")  # 如果当前价格等于目标均价，无法变动份额（分母为0）
            
        
        elif mode_input==3:
            print(f"当前价格：{cur_price}，当前份额：{num_now}，变动份额：{change_num:.0f}，变动后平均成本：{avg_cost:.4f}")# type: ignore
            print("修改后价格：")
            cur_price=float(input())
            if Mode==2:# type: ignore
                if target_price != cur_price:# type: ignore
                    change_num = int((target_price * num_now - volume) / (cur_price - target_price))# type: ignore
                    #转成100的倍数
                    if PRACTICE:
                        change_num=round(change_num / 100) * 100
                else:
                    change_num = float("inf")  # 如果当前价格等于目标均价，无法变动份额（分母为0）
        
        elif mode_input==4:
            print(f"当前价格：{cur_price}，当前份额：{num_now}，变动份额：{change_num:.0f}，变动后平均成本：{avg_cost:.4f}")# type: ignore
            print("修改后变动份额（手）：")
            change_num=int(input())*100

        
        elif mode_input==5:
            print(f"当前价格：{cur_price}，当前份额：{num_now}，变动份额：{change_num:.0f}，变动后平均成本：{avg_cost:.4f}")# type: ignore
            print("修改后目标均价：")
            target_price=float(input())
            if target_price != cur_price:# type: ignore
                change_num = int((target_price * num_now - volume) / (cur_price - target_price))# type: ignore
                #转成100的倍数
                if PRACTICE:
                    change_num=round(change_num / 100) * 100
            else:
                change_num = float("inf")  # 如果当前价格等于目标均价，无法变动份额（分母为0）

        #公共计算与输出
        volume1=volume+change_num*cur_price# type: ignore
        cur_volume=num_now*cur_price# type: ignore
        if num_now+change_num==0:# type: ignore
            avg_cost=float("inf")# type: ignore
            # pass
        else:
            avg_cost=volume1/(num_now+change_num)# type: ignore
        print("------------------")
        if volume1 < 0 and num_now+change_num >0:# type: ignore
            print("注意：总成本为负，意味着剩余持仓总额为纯利润")
        if num_now + change_num < 0 and cur_price>avg_cost:# type: ignore
            print(f"警告：在亏损的情况下若想使成本低于现价，需清仓后借入{abs(num_now + change_num):.0f}股做空")# type: ignore
        if num_now + change_num < 0 and cur_price<avg_cost:# type: ignore
            print(f"警告：在盈利的情况下若想使成本高于现价？需清仓后借入{abs(num_now + change_num):.0f}股做空")# type: ignore
        print(f"变动份额：{change_num:.0f}")# type: ignore
        print("当前总额：%.2f"%cur_volume)# type: ignore
        print(f"变动后总额：{cur_price*(num_now+change_num):.2f}")# type: ignore
        print("变动后平均成本：%.4f"%avg_cost)# type: ignore
        print("是否确认变动？（Y/N）",end="")
        confirm=input()
        if confirm.upper()=="Y":
            num_now+=change_num# type: ignore
            volume=volume1# type: ignore
            change_num=0
            print("变动成功！")
        print("------------------")

    except Exception as e:
        print("输入有误，返回主菜单！",end="")
        print(e)
        print("------------------")
