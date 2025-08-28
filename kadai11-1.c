#include<stdio.h>
#define N 1000
int a[N]
int input-students(){
    int n;
    printf("生徒の人数を入力してください")；
    scanf("%d,&n");

    if(n>N){
        printf("エラー：生徒の数が多すぎます。/n");
        return -1;
    }
    for(int i=0;i<n;i++){
        printf("生徒%dの点数を入力してください;"i+1);
        scanf("%d",&a[i]);
        if(a[i]<0||a[i]>100){
            printf("エラー;点数は０から100の間で入力してください。/n");
            return -1;
        }
        }
        return n;
}
        void calculate_and_display(int n){
            int sum=0;
            int max=a[0];
            int min=a[0];

            for(int i=0;i<n;i++){
                sum+=a[i];
                if(a[i]>max){
                    max=a[i];
                }
                if(a[i]<min){
                    min=a[i];
                }
            }
            double average=(double)sum/n;

            printf("平均点:%.2f\n",average);
            printf("最大得点:%d/n",max);
            printf("最少得点:%d/n",min);
        }

        int main(){
            int n=input_students();
            if(n>0){
                calculate_and_display(n);
            }
            return 0;
        }
    

